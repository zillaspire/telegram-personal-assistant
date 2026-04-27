from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class StateStore:
    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS app_state (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS event_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.commit()

    def get_json(self, key: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM app_state WHERE key = ?",
                (key,),
            ).fetchone()
        if not row:
            return default.copy() if default else {}
        return json.loads(row["value_json"])

    def set_json(self, key: str, value: dict[str, Any]) -> None:
        payload = json.dumps(value, ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO app_state (key, value_json)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, payload),
            )
            connection.commit()

    def log_event(self, kind: str, payload: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO event_log (kind, payload_json) VALUES (?, ?)",
                (kind, json.dumps(payload, ensure_ascii=False)),
            )
            connection.commit()

    def get_last_event(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, kind, payload_json, created_at
                FROM event_log
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload_json"])
        return {
            "id": row["id"],
            "kind": row["kind"],
            "payload": payload,
            "created_at": row["created_at"],
        }

