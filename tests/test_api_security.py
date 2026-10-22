from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault("LC_AUTH_SECRET", "test-secret-0123456789-0123456789")
os.environ.setdefault("LC_AI_PROVIDER", "mock")

from fastapi.testclient import TestClient

from apps.api.config import Settings
import apps.api.db as db_module
from apps.api.db import Database
from apps.api.main import create_app
from apps.api.providers import WhisperCppProvider
from apps.api.security import AUTH_COOKIE, create_auth_token, issue_pairing_code


def test_audio_temp_cleanup_is_ttl_scoped_to_dedicated_prefix(tmp_path: Path) -> None:
    active=settings(tmp_path); provider=WhisperCppProvider(active)
    stale=provider.temp_root/"lc-audio-stale"; fresh=provider.temp_root/"lc-audio-fresh"; unrelated=provider.temp_root/"keep-this"; stale.write_bytes(b"x"); fresh.write_bytes(b"x"); unrelated.write_bytes(b"x")
    assert provider._expired_audio_temp(stale.name,699,1000,300) is True
    assert provider._expired_audio_temp(fresh.name,701,1000,300) is False
    assert provider._expired_audio_temp(unrelated.name,0,1000,300) is False
    now=time.time(); os.utime(stale,(now-600,now-600)); os.utime(fresh,(now,now)); os.utime(unrelated,(now-600,now-600)); provider._cleanup_stale_temp()
    assert not stale.exists() and fresh.exists() and unrelated.exists()


def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_path=tmp_path / "test.db", ai_provider="mock", ollama_url="http://127.0.0.1:11434",
        ollama_model="gemma3:4b", whisper_url="http://127.0.0.1:8080/inference", cookie_secure=True,
        auth_secret="test-secret-0123456789-0123456789", allowed_origins=("https://testserver",),
    )


def pair(client: TestClient, application: object) -> dict[str, str]:
    code, _ = issue_pairing_code(application.state.db, application.state.settings)
    response = client.post("/api/auth/pair", json={"code": code}, headers={"Origin": "https://testserver"})
    assert response.status_code == 200
    return {"Origin": "https://testserver", "X-CSRF-Token": response.json()["csrf_token"]}


def test_pairing_is_one_time_origin_bound_and_rate_limited(tmp_path: Path) -> None:
    application = create_app(settings=settings(tmp_path))
    client = TestClient(application, base_url="https://testserver")
    code, _ = issue_pairing_code(application.state.db, application.state.settings)
    assert client.post("/api/auth/pair", json={"code": code}).status_code == 403
    assert client.post("/api/auth/pair", json={"code": code}, headers={"Origin":"https://testserver"}).status_code == 200
    second = TestClient(application, base_url="https://testserver")
    assert second.post("/api/auth/pair", json={"code": code}, headers={"Origin":"https://testserver"}).status_code == 401
    for _ in range(4):
        assert second.post("/api/auth/pair", json={"code":"ZZZZZZZZ"}, headers={"Origin":"https://testserver"}).status_code == 401
    assert second.post("/api/auth/pair", json={"code":"ZZZZZZZZ"}, headers={"Origin":"https://testserver"}).status_code == 429


def test_auth_csrf_and_owner_checks(tmp_path: Path) -> None:
    application = create_app(settings=settings(tmp_path))
    client = TestClient(application, base_url="https://testserver")
    headers = pair(client, application)
    no_csrf = client.post("/api/sessions",json={"scenario_id":"cafe-order","idempotency_key":"create-0001"},headers={"Origin":"https://testserver"})
    assert no_csrf.status_code == 403
    created = client.post("/api/sessions",json={"scenario_id":"cafe-order","idempotency_key":"create-0001"},headers=headers)
    assert created.status_code == 200
    session_id = created.json()["id"]
    other_token, _ = create_auth_token("another-user", application.state.settings)
    stranger = TestClient(application, base_url="https://testserver")
    stranger.cookies.set(AUTH_COOKIE, other_token)
    assert stranger.get(f"/api/sessions/{session_id}").status_code == 404


def test_unset_or_weak_secret_is_rejected(monkeypatch: object, tmp_path: Path) -> None:
    monkeypatch.delenv("LC_AUTH_SECRET", raising=False)
    monkeypatch.setenv("LC_DATABASE_PATH", str(tmp_path / "unused.db"))
    try:
        Settings.from_env()
    except RuntimeError as exc:
        assert "LC_AUTH_SECRET" in str(exc)
    else:
        raise AssertionError("weak default auth secrets must never be accepted")


def test_failed_migration_does_not_partially_apply(monkeypatch: object,tmp_path: Path) -> None:
    fake_root=tmp_path/"root"; migrations=fake_root/"migrations"; migrations.mkdir(parents=True)
    (migrations/"001_broken.sql").write_text("CREATE TABLE should_rollback(id INTEGER);\nTHIS IS NOT SQL;",encoding="utf-8")
    monkeypatch.setattr(db_module,"ROOT",fake_root); database=Database(tmp_path/"atomic.db")
    try: database.migrate()
    except Exception: pass
    else: raise AssertionError("broken migration must fail")
    with database.connect() as connection:
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='should_rollback'").fetchone() is None
        assert connection.execute("SELECT 1 FROM schema_migrations WHERE version='001_broken.sql'").fetchone() is None
