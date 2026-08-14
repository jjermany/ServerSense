import os
import tempfile

os.environ["SERVERSENSE_CONFIG_DIR"] = tempfile.mkdtemp(prefix="serversense-tests-")
os.environ["SERVERSENSE_SECRET_KEY"] = "tests-only-secret-key-that-is-long-enough"
os.environ["SERVERSENSE_DEMO_MODE"] = "false"
os.environ["SERVERSENSE_METRICS_INTERVAL_SECONDS"] = "3600"

import pytest
from fastapi.testclient import TestClient

from serversense.db import initialize_database
from serversense.main import app

initialize_database()


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def authenticated_client(client: TestClient) -> TestClient:
    status = client.get("/api/auth/status").json()
    if status["setup_required"]:
        response = client.post(
            "/api/auth/setup",
            json={
                "username": "administrator",
                "password": "correct horse battery staple",
                "server_name": "Test Tower",
                "demo_mode": True,
            },
        )
        assert response.status_code == 201
    elif client.get("/api/auth/me").status_code != 200:
        response = client.post(
            "/api/auth/login",
            json={"username": "administrator", "password": "correct horse battery staple"},
        )
        assert response.status_code == 200
    return client
