from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("LC_AUTH_SECRET", "test-secret-0123456789-0123456789")
os.environ.setdefault("LC_AI_PROVIDER", "mock")

from fastapi.testclient import TestClient

from apps.api.config import Settings
from apps.api.main import create_app
from apps.api.security import issue_pairing_code


def settings(tmp_path: Path, *, edge_secret: str = "") -> Settings:
    return Settings(
        database_path=tmp_path / "edge-boundary.db",
        ai_provider="mock",
        ollama_url="http://127.0.0.1:11434",
        ollama_model="gemma3:4b",
        whisper_url="http://127.0.0.1:8080/inference",
        cookie_secure=True,
        auth_secret="test-secret-0123456789-0123456789",
        allowed_origins=("https://language-conversation.pages.dev",),
        edge_secret=edge_secret,
    )


def test_edge_secret_is_optional_for_local_compatibility(tmp_path: Path) -> None:
    application = create_app(settings=settings(tmp_path))
    response = TestClient(application).get("/api/health")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"


def test_configured_edge_secret_guards_every_api_path(tmp_path: Path) -> None:
    application = create_app(settings=settings(tmp_path, edge_secret="edge-secret-value"))
    client = TestClient(application)

    missing = client.get("/api/health")
    wrong = client.get("/api/health", headers={"X-LC-Edge-Secret": "wrong"})
    unknown = client.get("/api/not-a-real-route")
    allowed = client.get("/api/health", headers={"X-LC-Edge-Secret": "edge-secret-value"})

    for rejected in (missing, wrong, unknown):
        assert rejected.status_code == 403
        assert rejected.json()["detail"]["code"] == "edge_access_rejected"
        assert rejected.headers["cache-control"] == "no-store"
        assert rejected.headers["x-robots-tag"] == "noindex, nofollow"
    assert allowed.status_code == 200


def test_edge_secret_uses_constant_time_comparison(monkeypatch: object, tmp_path: Path) -> None:
    application = create_app(settings=settings(tmp_path, edge_secret="edge-secret-value"))
    client = TestClient(application)
    comparisons: list[tuple[bytes, bytes]] = []

    def observe_comparison(supplied: bytes, expected: bytes) -> bool:
        comparisons.append((supplied, expected))
        return False

    monkeypatch.setattr("apps.api.main.hmac.compare_digest", observe_comparison)

    assert client.get("/api/health", headers={"X-LC-Edge-Secret": "wrong"}).status_code == 403
    assert comparisons == [(b"wrong", b"edge-secret-value")]


def test_non_api_backend_responses_are_noindex_but_not_forced_no_store(tmp_path: Path) -> None:
    application = create_app(settings=settings(tmp_path, edge_secret="edge-secret-value"))
    response = TestClient(application).get("/")

    assert response.status_code == 200
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert response.headers.get("cache-control") != "no-store"


def test_edge_pairing_rate_limit_uses_forwarded_client_identity(tmp_path: Path) -> None:
    application = create_app(settings=settings(tmp_path, edge_secret="edge-secret-value"))
    client = TestClient(application, base_url="https://language-conversation.pages.dev")
    base_headers = {
        "Origin": "https://language-conversation.pages.dev",
        "X-LC-Edge-Secret": "edge-secret-value",
    }

    for _ in range(application.state.settings.pairing_max_attempts):
        response = client.post(
            "/api/auth/pair",
            json={"code": "ZZZZZZZZ"},
            headers={**base_headers, "X-LC-Client-IP": "198.51.100.10"},
        )
        assert response.status_code == 401

    limited = client.post(
        "/api/auth/pair",
        json={"code": "ZZZZZZZZ"},
        headers={**base_headers, "X-LC-Client-IP": "198.51.100.10"},
    )
    independent = client.post(
        "/api/auth/pair",
        json={"code": "ZZZZZZZZ"},
        headers={**base_headers, "X-LC-Client-IP": "198.51.100.11"},
    )

    assert limited.status_code == 429
    assert independent.status_code == 401
    with application.state.db.connect() as connection:
        fingerprints = {
            row[0]
            for row in connection.execute("SELECT DISTINCT fingerprint FROM pairing_attempts")
        }
    assert "198.51.100.10" not in fingerprints
    assert "198.51.100.11" not in fingerprints
    assert len(fingerprints) == 2
    assert all(len(value) == 64 for value in fingerprints)


def test_local_pairing_ignores_untrusted_forwarded_client_identity(tmp_path: Path) -> None:
    application = create_app(settings=settings(tmp_path))
    client = TestClient(application, base_url="https://language-conversation.pages.dev")
    code, _ = issue_pairing_code(application.state.db, application.state.settings)

    response = client.post(
        "/api/auth/pair",
        json={"code": code},
        headers={
            "Origin": "https://language-conversation.pages.dev",
            "X-LC-Client-IP": "203.0.113.200",
        },
    )

    assert response.status_code == 200
    with application.state.db.connect() as connection:
        fingerprint = connection.execute(
            "SELECT fingerprint FROM pairing_attempts ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
    assert fingerprint != "203.0.113.200"


def test_pairing_prunes_attempts_outside_the_rate_window(tmp_path: Path) -> None:
    application = create_app(settings=settings(tmp_path, edge_secret="edge-secret-value"))
    with application.state.db.transaction(immediate=True) as connection:
        connection.execute(
            "INSERT INTO pairing_attempts(fingerprint, attempted_at, succeeded) VALUES (?, ?, ?)",
            ("expired-fingerprint", "2000-01-01T00:00:00+00:00", 0),
        )

    response = TestClient(
        application,
        base_url="https://language-conversation.pages.dev",
    ).post(
        "/api/auth/pair",
        json={"code": "ZZZZZZZZ"},
        headers={
            "Origin": "https://language-conversation.pages.dev",
            "X-LC-Edge-Secret": "edge-secret-value",
            "X-LC-Client-IP": "198.51.100.12",
        },
    )

    assert response.status_code == 401
    with application.state.db.connect() as connection:
        expired_count = connection.execute(
            "SELECT COUNT(*) FROM pairing_attempts WHERE fingerprint='expired-fingerprint'"
        ).fetchone()[0]
    assert expired_count == 0


def test_delete_all_removes_pairing_attempt_fingerprints(tmp_path: Path) -> None:
    application = create_app(settings=settings(tmp_path))
    client = TestClient(application, base_url="https://language-conversation.pages.dev")
    origin = "https://language-conversation.pages.dev"

    assert client.post(
        "/api/auth/pair",
        json={"code": "ZZZZZZZZ"},
        headers={"Origin": origin},
    ).status_code == 401
    code, _ = issue_pairing_code(application.state.db, application.state.settings)
    paired = client.post(
        "/api/auth/pair",
        json={"code": code},
        headers={"Origin": origin},
    )
    assert paired.status_code == 200

    deleted = client.delete(
        "/api/profile/data",
        headers={
            "Origin": origin,
            "X-CSRF-Token": paired.json()["csrf_token"],
            "X-Confirm-Delete": "all-learning-data",
        },
    )

    assert deleted.status_code == 200
    with application.state.db.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM pairing_attempts").fetchone()[0] == 0
