"""Optional authenticator MFA; secrets never leave the enrollment response."""

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta

import pyotp
import segno
from fastapi import HTTPException
from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from serversense.models import Session as LoginSession
from serversense.models import User
from serversense.security import LoginRateLimiter, password_hash
from serversense.services.secrets import decrypt_secret, encrypt_secret

management_limiter = LoginRateLimiter()


def now() -> datetime:
    return datetime.now(UTC)


def lock_user(db: Session, user: User) -> User:
    """Serialize factor changes, code consumption and session creation together."""
    user_id, verified_hash = user.id, user.password_hash
    db.rollback()
    db.execute(text("BEGIN IMMEDIATE"))
    locked = db.get(User, user_id, populate_existing=True)
    if locked is None or locked.password_hash != verified_hash:
        raise HTTPException(401, "Sign in again to continue")
    return locked


def authorize_change(db: Session, user: User, password: str) -> User:
    management_limiter.check(str(user.id))
    if not password_hash.verify(password, user.password_hash):
        raise HTTPException(401, "Invalid password")
    return lock_user(db, user)


def status(user: User) -> dict:
    return {
        "enabled": user.mfa_secret is not None,
        "recovery_codes_remaining": len(user.mfa_recovery_hashes or []),
    }


def start_enrollment(db: Session, user: User, password: str) -> dict:
    user = authorize_change(db, user, password)
    if user.mfa_secret is not None:
        raise HTTPException(409, "MFA is already enabled")
    secret = pyotp.random_base32()
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.username, issuer_name="ServerSense")
    qr = segno.make_qr(uri).png_data_uri(scale=6)
    user.mfa_pending_secret = encrypt_secret(secret)
    user.mfa_pending_expires_at = now() + timedelta(minutes=10)
    db.commit()
    return {"secret": secret, "qr_code": qr, "expires_in_seconds": 600}


def _matching_step(secret: str, code: str, last_step: int | None) -> int | None:
    if not secret or not re.fullmatch(r"[0-9]{6}", code):
        return None
    current = int(now().timestamp()) // 30
    totp = pyotp.TOTP(secret)
    for step in (current, current - 1, current + 1):
        if (last_step is None or step > last_step) and hmac.compare_digest(
            totp.at(step * 30), code
        ):
            return step
    return None


def _recovery_hash(code: str) -> str:
    normalized = code.replace("-", "").replace(" ", "").upper()
    return hashlib.sha256(normalized.encode()).hexdigest()


def _new_recovery_codes(user: User) -> list[str]:
    codes = []
    for _ in range(10):
        raw = secrets.token_hex(10).upper()
        codes.append("-".join(raw[index : index + 4] for index in range(0, 20, 4)))
    user.mfa_recovery_hashes = [_recovery_hash(code) for code in codes]
    return codes


def consume_code(user: User, code: str) -> None:
    """Caller must hold the SQLite writer lock through the subsequent commit."""
    if user.mfa_secret is None:
        raise HTTPException(409, "MFA is not enabled")
    code = code.strip()
    step = _matching_step(decrypt_secret(user.mfa_secret), code, user.mfa_last_step)
    if step is not None:
        user.mfa_last_step = step
        return
    supplied = _recovery_hash(code)
    hashes = user.mfa_recovery_hashes or []
    for saved in hashes:
        if hmac.compare_digest(saved, supplied):
            user.mfa_recovery_hashes = [value for value in hashes if value != saved]
            return
    raise HTTPException(
        401, "Invalid or already used code. Try the next authenticator code or a recovery code."
    )


def _revoke_sessions(db: Session, user: User) -> None:
    db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))


def confirm_enrollment(db: Session, user: User, password: str, code: str) -> list[str]:
    user = authorize_change(db, user, password)
    if user.mfa_secret is not None:
        raise HTTPException(409, "MFA is already enabled")
    expires = user.mfa_pending_expires_at
    if not user.mfa_pending_secret or not expires or expires.replace(tzinfo=UTC) <= now():
        raise HTTPException(400, "MFA setup expired. Start setup again.")
    step = _matching_step(decrypt_secret(user.mfa_pending_secret), code.strip(), None)
    if step is None:
        raise HTTPException(401, "Invalid authenticator code")
    user.mfa_secret = user.mfa_pending_secret
    user.mfa_pending_secret = None
    user.mfa_pending_expires_at = None
    user.mfa_last_step = step
    codes = _new_recovery_codes(user)
    _revoke_sessions(db, user)
    return codes


def disable(db: Session, user: User, password: str, code: str) -> None:
    user = authorize_change(db, user, password)
    consume_code(user, code)
    user.mfa_secret = None
    user.mfa_pending_secret = None
    user.mfa_pending_expires_at = None
    user.mfa_last_step = None
    user.mfa_recovery_hashes = None
    _revoke_sessions(db, user)


def regenerate_recovery_codes(db: Session, user: User, password: str, code: str) -> list[str]:
    user = authorize_change(db, user, password)
    consume_code(user, code)
    codes = _new_recovery_codes(user)
    _revoke_sessions(db, user)
    return codes
