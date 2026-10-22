from __future__ import annotations

from .config import Settings
from .db import Database
from .security import issue_pairing_code


def main() -> None:
    settings = Settings.from_env()
    db = Database(settings.database_path)
    db.migrate()
    code, expires_at = issue_pairing_code(db, settings)
    print(f"Pairing code: {code}")
    print(f"Expires at: {expires_at}")


if __name__ == "__main__":
    main()
