from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]


def _csv(name: str, default: str) -> tuple[str, ...]:
    return tuple(item.strip().rstrip("/") for item in os.getenv(name, default).split(",") if item.strip())


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path
    ai_provider: str
    ollama_url: str
    ollama_model: str
    whisper_url: str
    cookie_secure: bool
    auth_secret: str
    allowed_origins: tuple[str, ...]
    edge_secret: str = ""
    session_ttl_seconds: int = 60 * 60 * 24 * 30
    pairing_ttl_seconds: int = 300
    pairing_max_attempts: int = 5
    pairing_window_seconds: int = 600
    max_audio_bytes: int = 10 * 1024 * 1024
    max_audio_seconds: int = 45
    audio_temp_ttl_seconds: int = 5 * 60

    @classmethod
    def from_env(cls) -> "Settings":
        provider = os.getenv("LC_AI_PROVIDER", "ollama").strip().lower()
        if provider not in {"ollama", "mock"}:
            raise ValueError("LC_AI_PROVIDER must be 'ollama' or explicit test-only 'mock'")
        secret = os.getenv("LC_AUTH_SECRET", "")
        if len(secret) < 32 or secret == "local-development-change-me":
            raise RuntimeError("LC_AUTH_SECRET must be set to a random value of at least 32 characters")
        ollama_url = os.getenv("LC_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
        whisper_url = os.getenv("LC_WHISPER_URL", "http://127.0.0.1:8080/inference")
        for name, value in (("LC_OLLAMA_URL", ollama_url), ("LC_WHISPER_URL", whisper_url)):
            parsed = urlsplit(value)
            if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.query or parsed.fragment:
                raise RuntimeError(f"{name} must be an HTTP loopback URL without credentials, query, or fragment")
        model = os.getenv("LC_MODEL", "gemma3:4b")
        if model.endswith(":cloud"):
            raise RuntimeError("LC_MODEL must be an installed local Ollama model; cloud models are disabled")
        return cls(
            database_path=Path(os.getenv("LC_DATABASE_PATH", str(ROOT / "data" / "language-conversation.db"))),
            ai_provider=provider,
            ollama_url=ollama_url,
            ollama_model=model,
            whisper_url=whisper_url,
            cookie_secure=os.getenv("LC_COOKIE_SECURE", "1") != "0",
            auth_secret=secret,
            allowed_origins=_csv(
                "LC_ALLOWED_ORIGINS",
                "https://localhost:5173,https://127.0.0.1:5173,https://testserver",
            ),
            edge_secret=os.getenv("LC_EDGE_SECRET", ""),
        )
