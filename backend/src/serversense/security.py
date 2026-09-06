import hashlib
import secrets
import threading
import time
from collections import OrderedDict, deque
from datetime import UTC, datetime, timedelta

from fastapi import Cookie, Depends, HTTPException, Response, status
from pwdlib import PasswordHash
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DBSession

from serversense.config import get_settings
from serversense.db import get_db
from serversense.models import Session, User

password_hash = PasswordHash.recommended()
DUMMY_PASSWORD_HASH = password_hash.hash(secrets.token_urlsafe(32))
COOKIE_NAME = "serversense_session"


class LoginRateLimiter:
    def __init__(self, attempts: int = 8, window_seconds: int = 60, max_keys: int = 4096):
        self.attempts = attempts
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._entries: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            # Entries are ordered by their most recent accepted attempt. Expire
            # idle keys without scanning the entire map on every login.
            while self._entries:
                oldest = next(iter(self._entries))
                if now - self._entries[oldest][-1] <= self.window_seconds:
                    break
                self._entries.popitem(last=False)
            if key not in self._entries:
                if len(self._entries) >= self.max_keys:
                    raise HTTPException(
                        status.HTTP_429_TOO_MANY_REQUESTS, "Too many login attempts"
                    )
                self._entries[key] = deque()
            entries = self._entries[key]
            while entries and now - entries[0] > self.window_seconds:
                entries.popleft()
            if len(entries) >= self.attempts:
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many login attempts")
            entries.append(now)
            self._entries.move_to_end(key)

    def reset(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)


login_limiter = LoginRateLimiter()
login_source_limiter = LoginRateLimiter(attempts=40)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: DBSession, user: User, response: Response) -> None:
    settings = get_settings()
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(hours=settings.session_hours)
    db.add(Session(token_hash=hash_token(token), user_id=user.id, expires_at=expires))
    db.commit()
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="strict",
        path="/",
    )


def clear_session(db: DBSession, response: Response, token: str | None) -> None:
    if token:
        db.execute(delete(Session).where(Session.token_hash == hash_token(token)))
        db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")


def current_user(
    token: str | None = Cookie(default=None, alias=COOKIE_NAME),
    db: DBSession = Depends(get_db),
) -> User:
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    session = db.scalar(
        select(Session).where(
            Session.token_hash == hash_token(token), Session.expires_at > datetime.now(UTC)
        )
    )
    if not session:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    return session.user
