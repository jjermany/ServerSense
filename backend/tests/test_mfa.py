from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from serversense.db import SessionLocal
from serversense.main import app
from serversense.models import Session as LoginSession
from serversense.models import User
from serversense.security import COOKIE_NAME, login_limiter
from serversense.services import mfa
from serversense.services.secrets import decrypt_secret

PASSWORD = "correct horse battery staple"
HEADERS = {"X-ServerSense-Request": "1"}
NOW = datetime(2026, 9, 6, 16, 0, tzinfo=UTC)


@pytest.fixture
def account(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    mfa.management_limiter._entries.clear()
    monkeypatch.setattr(mfa, "now", lambda: NOW)
    user_id = authenticated_client.get("/api/auth/me").json()["id"]
    yield authenticated_client
    with SessionLocal() as db:
        user = db.get(User, user_id)
        assert user is not None
        user.mfa_secret = None
        user.mfa_pending_secret = None
        user.mfa_pending_expires_at = None
        user.mfa_last_step = None
        user.mfa_recovery_hashes = None
        db.commit()


def enroll(client: TestClient) -> tuple[str, list[str]]:
    setup = client.post("/api/auth/mfa/setup", json={"password": PASSWORD})
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    result = client.post(
        "/api/auth/mfa/confirm",
        json={"password": PASSWORD, "code": pyotp.TOTP(secret).at(NOW)},
    )
    assert result.status_code == 200
    return secret, result.json()["recovery_codes"]


def login(client: TestClient, code: str | None = None, password: str = PASSWORD):
    return client.post(
        "/api/auth/login",
        json={
            "username": "ADMINISTRATOR",
            "password": password,
            **({"code": code} if code is not None else {}),
        },
    )


def test_mfa_is_opt_in_and_enrollment_is_private(account: TestClient) -> None:
    assert account.get("/api/auth/mfa").json() == {"enabled": False, "recovery_codes_remaining": 0}
    assert account.post("/api/auth/mfa/setup", json={"password": "wrong"}).status_code == 401
    response = account.post("/api/auth/mfa/setup", json={"password": PASSWORD})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert result["qr_code"].startswith("data:image/png;base64,")
    assert result["expires_in_seconds"] == 600
    with SessionLocal() as db:
        user = db.scalar(select(User))
        assert user is not None
        assert user.mfa_secret is None
        assert user.mfa_pending_secret != result["secret"]
        assert decrypt_secret(user.mfa_pending_secret or "") == result["secret"]
    assert (
        account.post(
            "/api/auth/mfa/confirm", json={"password": PASSWORD, "code": "bad"}
        ).status_code
        == 401
    )
    account.post("/api/auth/logout")
    assert login(account).status_code == 200
    assert "mfa_required" not in login(account).json()
    assert "mfa_pending_secret" not in account.get("/api/auth/me").json()
    anonymous = TestClient(app, headers=HEADERS)
    assert anonymous.get("/api/auth/mfa").status_code == 401
    for path in ("setup", "confirm", "disable", "recovery-codes"):
        assert (
            anonymous.post(
                f"/api/auth/mfa/{path}", json={"password": PASSWORD, "code": "123456"}
            ).status_code
            == 401
        )


def test_mfa_enrollment_expires_and_cannot_replace_an_enabled_factor(
    account: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = account.post("/api/auth/mfa/setup", json={"password": PASSWORD}).json()["secret"]
    monkeypatch.setattr(mfa, "now", lambda: NOW + timedelta(minutes=11))
    assert (
        account.post(
            "/api/auth/mfa/confirm",
            json={"password": PASSWORD, "code": pyotp.TOTP(secret).at(NOW + timedelta(minutes=11))},
        ).status_code
        == 400
    )
    assert account.get("/api/auth/mfa").json()["enabled"] is False
    monkeypatch.setattr(mfa, "now", lambda: NOW)
    enroll(account)
    assert account.post("/api/auth/mfa/setup", json={"password": PASSWORD}).status_code == 409


def test_mfa_login_requires_both_factors_and_rejects_replay(account: TestClient) -> None:
    old_cookie = account.cookies.get(COOKIE_NAME)
    secret, codes = enroll(account)
    assert len(codes) == len(set(codes)) == 10
    assert account.cookies.get(COOKIE_NAME) != old_cookie
    old_session = TestClient(app, headers=HEADERS)
    old_session.cookies.set(COOKIE_NAME, old_cookie or "")
    assert old_session.get("/api/auth/me").status_code == 401
    with SessionLocal() as db:
        user = db.scalar(select(User))
        assert user is not None
        assert user.mfa_secret != secret
        assert decrypt_secret(user.mfa_secret or "") == secret
        assert not set(codes).intersection(user.mfa_recovery_hashes or [])
    account.post("/api/auth/logout")
    response = login(account)
    assert response.json() == {"mfa_required": True}
    assert "set-cookie" not in response.headers
    assert account.get("/api/dashboard").status_code == 401
    assert login(account, pyotp.TOTP(secret).at(NOW)).status_code == 401
    assert login(account, codes[0], password="wrong").status_code == 401
    next_code = pyotp.TOTP(secret).at(NOW + timedelta(seconds=30))
    assert login(account, next_code).status_code == 200
    account.post("/api/auth/logout")
    # Persisted replay protection survives new browser clients and limiter resets.
    login_limiter._entries.clear()
    another = TestClient(app, headers=HEADERS)
    assert login(another, next_code).status_code == 401
    assert login(another, codes[0].lower().replace("-", " ")).status_code == 200
    another.post("/api/auth/logout")
    assert login(another, codes[0]).status_code == 401
    assert login(another, codes[1]).status_code == 200
    assert another.get("/api/auth/mfa").json()["recovery_codes_remaining"] == 8


def test_recovery_code_consumption_is_atomic(account: TestClient) -> None:
    _, codes = enroll(account)

    def attempt() -> int:
        client = TestClient(app, headers=HEADERS)
        return login(client, codes[0]).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(results) == [200, 401]


def test_mfa_changes_require_reauthentication_and_rotate_sessions(account: TestClient) -> None:
    _, codes = enroll(account)
    for path in ("disable", "recovery-codes"):
        assert (
            account.post(
                f"/api/auth/mfa/{path}", json={"password": "wrong", "code": codes[0]}
            ).status_code
            == 401
        )
        assert (
            account.post(
                f"/api/auth/mfa/{path}", json={"password": PASSWORD, "code": "bad"}
            ).status_code
            == 401
        )
    result = account.post(
        "/api/auth/mfa/recovery-codes", json={"password": PASSWORD, "code": codes[0]}
    )
    assert result.status_code == 200
    replacements = result.json()["recovery_codes"]
    assert not set(codes).intersection(replacements)
    assert (
        account.post(
            "/api/auth/mfa/disable", json={"password": PASSWORD, "code": codes[1]}
        ).status_code
        == 401
    )
    # Start a fresh limiter window after deliberately exhausting invalid attempts.
    mfa.management_limiter._entries.clear()
    assert (
        account.post(
            "/api/auth/mfa/disable", json={"password": PASSWORD, "code": replacements[0]}
        ).status_code
        == 200
    )
    assert account.get("/api/auth/mfa").json() == {"enabled": False, "recovery_codes_remaining": 0}
    with SessionLocal() as db:
        user = db.scalar(select(User))
        assert user is not None
        assert user.mfa_secret is None
        assert user.mfa_recovery_hashes is None
        assert (
            len(db.scalars(select(LoginSession).where(LoginSession.user_id == user.id)).all()) == 1
        )
    account.post("/api/auth/logout")
    assert "mfa_required" not in login(account).json()


def test_mfa_attempts_cannot_reset_login_limit_with_valid_password(account: TestClient) -> None:
    enroll(account)
    account.post("/api/auth/logout")
    login_limiter._entries.clear()
    for _ in range(8):
        assert login(account, "bad").status_code == 401
    assert login(account).status_code == 429


def test_totp_window_rejects_old_codes(account: TestClient) -> None:
    secret, _ = enroll(account)
    account.post("/api/auth/logout")
    assert login(account, pyotp.TOTP(secret).at(NOW - timedelta(seconds=60))).status_code == 401
    assert login(account, pyotp.TOTP(secret).at(NOW + timedelta(seconds=60))).status_code == 401


def test_mfa_management_is_rate_limited(account: TestClient) -> None:
    for _ in range(8):
        assert account.post("/api/auth/mfa/setup", json={"password": "wrong"}).status_code == 401
    assert account.post("/api/auth/mfa/setup", json={"password": PASSWORD}).status_code == 429
