from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from serversense.api import auth
from serversense.config import Settings
from serversense.db import Base, get_db
from serversense.middleware import MAX_API_BODY_BYTES
from serversense.models import User
from serversense.security import LoginRateLimiter
from serversense.services.urls import validate_http_url


@pytest.mark.parametrize(
    "url",
    [
        "file:///config/secrets",
        "http://admin:secret@model.test",
        "http://host:99999",
        "http://host:wrong",
        "http://host\n.test",
        "http://host/#fragment",
    ],
)
def test_endpoint_validation_rejects_credentials_and_malformed_urls(url: str) -> None:
    with pytest.raises(ValueError):
        validate_http_url(url)


def test_endpoint_validation_preserves_lan_services_and_webhook_queries() -> None:
    for url in (
        "http://ollama:11434",
        "http://192.168.1.5:8989/sonarr",
        "https://hooks.test/?token=abc",
    ):
        assert validate_http_url(url) == url


def test_installation_requires_an_explicit_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SERVERSENSE_SECRET_KEY")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_login_limiter_bounds_keys_and_expires_idle_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100.0]
    monkeypatch.setattr("serversense.security.time.monotonic", lambda: now[0])
    limiter = LoginRateLimiter(attempts=2, window_seconds=60, max_keys=2)
    limiter.check("a")
    limiter.check("b")
    with pytest.raises(HTTPException) as full:
        limiter.check("c")
    assert full.value.status_code == 429
    limiter.check("a")
    with pytest.raises(HTTPException):
        limiter.check("a")
    assert len(limiter._entries) == 2
    now[0] += 61
    limiter.check("c")
    assert list(limiter._entries) == ["c"]


def test_login_source_limit_cannot_be_bypassed_by_rotating_usernames(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth, "login_source_limiter", LoginRateLimiter(attempts=2))
    for username in ("missing-one", "missing-two"):
        assert (
            client.post(
                "/api/auth/login", json={"username": username, "password": "wrong-password"}
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/api/auth/login", json={"username": "missing-three", "password": "wrong-password"}
        ).status_code
        == 429
    )


def test_browser_form_mutations_are_rejected(authenticated_client: TestClient) -> None:
    client = authenticated_client
    response = client.post(
        "/api/auth/logout",
        headers={"X-ServerSense-Request": "", "Origin": "https://untrusted.example"},
    )
    assert response.status_code == 403
    assert client.get("/api/auth/me").status_code == 200
    response = client.options(
        "/api/auth/logout",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-ServerSense-Request",
        },
    )
    assert "access-control-allow-origin" not in response.headers
    assert client.post("/api/auth/logout").status_code == 204


def test_api_limits_body_and_does_not_echo_secrets(client: TestClient) -> None:
    password = "secret-value-" * 30
    response = client.post("/api/auth/login", json={"username": "admin", "password": password})
    assert response.status_code == 422
    assert password not in response.text
    assert all("input" not in error for error in response.json()["detail"])
    response = client.post(
        "/api/auth/login",
        content=iter([b"x" * 1024 for _ in range(MAX_API_BODY_BYTES // 1024 + 1)]),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


def test_concurrent_setup_creates_exactly_one_account(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'setup.db'}")
    Base.metadata.create_all(engine)
    application = FastAPI()
    application.include_router(auth.router)

    def database() -> Iterator[Session]:
        with Session(engine) as db:
            yield db

    application.dependency_overrides[get_db] = database
    barrier = Barrier(2)
    hashed = auth.password_hash.hash("correct horse battery staple")

    def slow_hash(_: str) -> str:
        barrier.wait(timeout=10)
        return hashed

    monkeypatch.setattr(auth, "password_hash", SimpleNamespace(hash=slow_hash))
    monkeypatch.setattr(auth, "login_source_limiter", LoginRateLimiter())

    def submit(username: str) -> int:
        with TestClient(application) as client:
            return client.post(
                "/api/auth/setup",
                json={"username": username, "password": "correct horse battery staple"},
            ).status_code

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(submit, ("first-admin", "second-admin"))) == [201, 409]
        with Session(engine) as db:
            assert db.scalar(select(func.count(User.id))) == 1
    finally:
        engine.dispose()
