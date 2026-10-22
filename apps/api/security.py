from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException, Request

from .config import Settings
from .db import Database, LOCAL_USER_ID, iso_now, utc_now


AUTH_COOKIE = "lc_session"
CSRF_COOKIE = "lc_csrf"


def api_error(status: int, code: str, message: str, *, retryable: bool = False) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, "retryable": retryable})


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _sign(payload: bytes, secret: str) -> str:
    return _b64(hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest())


def create_auth_token(user_id: str, settings: Settings) -> tuple[str, str]:
    expires = int((utc_now() + timedelta(seconds=settings.session_ttl_seconds)).timestamp())
    data = {"user_id": user_id, "expires": expires, "nonce": secrets.token_urlsafe(12)}
    encoded = _b64(json.dumps(data, separators=(",", ":")).encode("utf-8"))
    return f"{encoded}.{_sign(encoded.encode('ascii'), settings.auth_secret)}", str(expires)


def verify_auth_token(token: str, settings: Settings) -> str | None:
    try:
        encoded, signature = token.rsplit(".", 1)
        if not hmac.compare_digest(signature, _sign(encoded.encode("ascii"), settings.auth_secret)):
            return None
        padding = "=" * (-len(encoded) % 4)
        data = json.loads(base64.urlsafe_b64decode(encoded + padding))
        if int(data["expires"]) <= int(utc_now().timestamp()):
            return None
        return str(data["user_id"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def require_user(request: Request) -> str:
    settings: Settings = request.app.state.settings
    token = request.cookies.get(AUTH_COOKIE)
    user_id = verify_auth_token(token or "", settings)
    if user_id is None:
        raise api_error(401, "authentication_required", "Pair this device before using learning data.")
    return user_id


def validate_origin(request: Request, settings: Settings) -> None:
    origin = request.headers.get("origin", "").rstrip("/")
    if origin not in settings.allowed_origins:
        raise api_error(403, "origin_rejected", "The request origin is not allowed.")


def validate_csrf(request: Request) -> None:
    header = request.headers.get("x-csrf-token")
    cookie = request.cookies.get(CSRF_COOKIE)
    if not header or not cookie or not hmac.compare_digest(header, cookie):
        raise api_error(403, "csrf_rejected", "A valid CSRF token is required.")


def _code_hash(code: str, salt: bytes) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", code.encode("ascii"), salt, 200_000)
    return f"{salt.hex()}${digest.hex()}"


def issue_pairing_code(db: Database, settings: Settings) -> tuple[str, str]:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    code = "".join(secrets.choice(alphabet) for _ in range(8))
    salt = secrets.token_bytes(16)
    expires = utc_now() + timedelta(seconds=settings.pairing_ttl_seconds)
    with db.transaction(immediate=True) as connection:
        connection.execute(
            "INSERT INTO pairing_sessions(id, code_hash, expires_at, authorized_user_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (str(uuid4()), _code_hash(code, salt), expires.isoformat(), LOCAL_USER_ID, iso_now()),
        )
    return code, expires.isoformat()


def consume_pairing_code(db: Database, settings: Settings, code: str, fingerprint: str) -> str:
    cutoff = (utc_now() - timedelta(seconds=settings.pairing_window_seconds)).isoformat()
    now = iso_now()
    fingerprint_hash = hmac.new(
        settings.auth_secret.encode("utf-8"),
        fingerprint.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    failure: HTTPException | None = None
    authorized_user_id: str | None = None
    with db.transaction(immediate=True) as connection:
        connection.execute("DELETE FROM pairing_attempts WHERE attempted_at<?", (cutoff,))
        attempts = connection.execute(
            "SELECT COUNT(*) FROM pairing_attempts WHERE fingerprint=? AND attempted_at>=? AND succeeded=0",
            (fingerprint_hash, cutoff),
        ).fetchone()[0]
        if attempts >= settings.pairing_max_attempts:
            failure = api_error(429, "pairing_rate_limited", "Too many pairing attempts. Try again later.", retryable=True)
        else:
            matches = connection.execute(
                "SELECT * FROM pairing_sessions WHERE consumed_at IS NULL AND expires_at>?", (now,)
            ).fetchall()
            matched = None
            for row in matches:
                salt_hex, _ = row["code_hash"].split("$", 1)
                if hmac.compare_digest(row["code_hash"], _code_hash(code, bytes.fromhex(salt_hex))):
                    matched = row
                    break
            connection.execute(
                "INSERT INTO pairing_attempts(fingerprint, attempted_at, succeeded) VALUES (?, ?, ?)",
                (fingerprint_hash, now, 1 if matched else 0),
            )
            if matched is None:
                failure = api_error(401, "invalid_pairing_code", "The pairing code is invalid, expired, or already used.")
            else:
                changed = connection.execute(
                    "UPDATE pairing_sessions SET consumed_at=? WHERE id=? AND consumed_at IS NULL", (now, matched["id"])
                ).rowcount
                if changed != 1:
                    failure = api_error(401, "invalid_pairing_code", "The pairing code was already used.")
                else:
                    authorized_user_id = str(matched["authorized_user_id"])
    if failure:
        raise failure
    assert authorized_user_id is not None
    return authorized_user_id


@dataclass(frozen=True, slots=True)
class Owner:
    user_id: str
