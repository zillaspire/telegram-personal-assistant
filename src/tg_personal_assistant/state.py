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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS outgoing (
                    chat_id INTEGER NOT NULL,
                    operation TEXT NOT NULL,
                    anchor_id INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL,
                    outgoing_id INTEGER,
                    PRIMARY KEY (chat_id, operation)
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_outgoing_per_chat
                ON outgoing(chat_id) WHERE outgoing_id IS NULL
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



    def reserve_outgoing(
        self, chat_id: int, operation: str, anchor_id: int, fingerprint: str,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO outgoing VALUES (?, ?, ?, ?, NULL)",
                (chat_id, operation, anchor_id, fingerprint),
            )
            return cursor.rowcount == 1

    def pending_outgoing(self, chat_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM outgoing WHERE chat_id = ? AND outgoing_id IS NULL",
                (chat_id,),
            ).fetchone()
        return dict(row) if row else None

    def complete_outgoing(self, chat_id: int, operation: str, outgoing_id: int) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE outgoing SET outgoing_id = ? WHERE chat_id = ? "
                "AND operation = ? AND outgoing_id IS NULL",
                (outgoing_id, chat_id, operation),
            )
            if cursor.rowcount != 1:
                return
            row = connection.execute(
                "SELECT value_json FROM app_state WHERE key = 'autonomous'"
            ).fetchone()
            session = json.loads(row[0]) if row else {}
            prefix = f"autonomous:{session.get('id')}:"
            if session.get("chat_id") != chat_id or not operation.startswith(prefix):
                return
            session["sent"] += 1
            session["last_outgoing_id"] = outgoing_id
            session["last_operation"] = operation
            connection.execute(
                "UPDATE app_state SET value_json = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE key = 'autonomous'",
                (json.dumps(session, ensure_ascii=False),),
            )


    def abandon_outgoing(self, chat_id: int, operation: str) -> None:
        # Keep the operation key as a tombstone to prevent replay.
        with self._connect() as connection:
            connection.execute(
                "UPDATE outgoing SET outgoing_id = 0 WHERE chat_id = ? "
                "AND operation = ? AND outgoing_id IS NULL",
                (chat_id, operation),
            )
