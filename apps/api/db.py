from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from .config import ROOT


LOCAL_USER_ID = "local-user"


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utc_now().isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.path,
            timeout=10,
            isolation_level=None,
            check_same_thread=False,
            factory=ClosingConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def migrate(self) -> None:
        with self.connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            for path in sorted((ROOT / "migrations").glob("*.sql")):
                if connection.execute("SELECT 1 FROM schema_migrations WHERE version=?", (path.name,)).fetchone():
                    continue
                version=path.name.replace("'","''"); applied=iso_now().replace("'","''")
                try:
                    connection.executescript(
                        "BEGIN IMMEDIATE;\n" + path.read_text(encoding="utf-8")
                        + f"\nINSERT INTO schema_migrations(version, applied_at) VALUES ('{version}', '{applied}');\nCOMMIT;"
                    )
                except Exception:
                    connection.rollback()
                    raise
            connection.execute(
                "INSERT OR IGNORE INTO users(id, locale, timezone, created_at) VALUES (?, 'ko-KR', 'Asia/Seoul', ?)",
                (LOCAL_USER_ID, iso_now()),
            )
            connection.execute("INSERT OR IGNORE INTO profiles(user_id) VALUES (?)", (LOCAL_USER_ID,))

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def one(self, sql: str, params: tuple[object, ...] = ()) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(sql, params).fetchone()

    def all(self, sql: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(connection.execute(sql, params).fetchall())


def row_dict(row: sqlite3.Row) -> dict[str, object]:
    return {key: row[key] for key in row.keys()}


def json_load(value: str | None, default: object) -> object:
    return json.loads(value) if value else default
