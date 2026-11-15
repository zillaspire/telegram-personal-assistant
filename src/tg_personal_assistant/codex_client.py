
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx


MODEL = "claude-sonnet-5-5"
API_URL = "https://api.onekey-ai.lol/v1/messages"

DEFAULT_SYSTEM = """
Ты ИИ-собеседник в Telegram.

Характер:
- Дружелюбный, общительный, остроумный.
- Общайся естественно и непринуждённо.
- Учитывай настроение собеседника.
- Поддерживай контекст разговора.
- Используй юмор, когда это уместно.

Стиль общения:
- Обычно отвечай коротко, как в мессенджере.
- Не пиши длинные объяснения без необходимости.
- Не задавай вопрос после каждого сообщения.
- Не повторяй одинаковые фразы.
- Иногда используй эмодзи.
- Не выдумывай воспоминания.
- Не скрывай, что ты искусственный интеллект.

Следуй дополнительным инструкциям личности,
если они предоставлены.
""".strip()


@dataclass(slots=True)
class CodexReply:
    text: str
    thread_id: str | None


class CodexCliClient:
    def __init__(
        self,
        model: str,
        *,
        codex_command: str = "codex",
        timeout_sec: int = 180,
        workspace_dir=None,
    ) -> None:
        self._model = MODEL

        self._key = (
            os.environ.get("ONEKEY_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")
        )

        if not self._key:
            raise RuntimeError(
                "ONEKEY_API_KEY is not configured"
            )

        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                timeout_sec,
                connect=20.0,
            )
        )

        db_path = Path(
            os.environ.get(
                "CLAUDE_SESSION_DB",
                "data/claude_sessions.sqlite3",
            )
        )

        db_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._db = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
        )

        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                initial_prompt TEXT NOT NULL,
                messages TEXT NOT NULL
            )
            """
        )
        self._db.commit()

        self._db_lock = asyncio.Lock()

    async def close(self) -> None:
        await self._client.aclose()
        self._db.close()

    async def _request(
        self,
        messages: list[dict[str, str]],
        system: str = DEFAULT_SYSTEM,
        max_tokens: int = 400,
    ) -> str:

        if not messages:
            raise ValueError("No messages provided")

        response = await self._client.post(
            API_URL,
            headers={
                "x-api-key": self._key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self._model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": messages,
            },
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"OneKey API error "
                f"{response.status_code}"
            )

        result = response.json()

        answer = "\n".join(
            block.get("text", "")
            for block in result.get("content", [])
            if block.get("type") == "text"
        ).strip()

        if not answer:
            raise RuntimeError(
                "Claude returned an empty response"
            )

        return answer

    async def generate_reply(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.8,
        max_tokens: int = 300,
    ) -> str:

        clean = [
            {
                "role": item["role"],
                "content": item["content"],
            }
            for item in messages
            if item.get("role") in ("user", "assistant")
            and item.get("content")
        ]

        return await self._request(
            clean,
            system=(
                DEFAULT_SYSTEM
                + "\n\nДополнительные инструкции:\n"
                + system_prompt
            ),
            max_tokens=max_tokens,
        )

    def _save_session(
        self,
        thread_id: str,
        initial_prompt: str,
        messages: list[dict[str, str]],
    ) -> None:

        self._db.execute(
            """
            INSERT OR REPLACE INTO sessions
            (id, initial_prompt, messages)
            VALUES (?, ?, ?)
            """,
            (
                thread_id,
                initial_prompt,
                json.dumps(
                    messages,
                    ensure_ascii=False,
                ),
            ),
        )
        self._db.commit()

    def _load_session(self, thread_id: str):

        row = self._db.execute(
            """
            SELECT initial_prompt, messages
            FROM sessions
            WHERE id = ?
            """,
            (thread_id,),
        ).fetchone()

        if not row:
            return None

        return row[0], json.loads(row[1])

    async def start_session(
        self,
        prompt: str,
    ) -> CodexReply:

        session_id = str(uuid.uuid4())

        messages = [
            {
                "role": "user",
                "content": prompt,
            }
        ]

        answer = await self._request(messages)

        messages.append({
            "role": "assistant",
            "content": answer,
        })

        async with self._db_lock:
            self._save_session(
                session_id,
                prompt,
                messages,
            )

        return CodexReply(
            text=answer,
            thread_id=session_id,
        )

    async def resume_session(
        self,
        thread_id: str,
        prompt: str,
    ) -> CodexReply:

        async with self._db_lock:
            session = self._load_session(thread_id)

        if session is None:
            raise RuntimeError(
                f"Session not found: {thread_id}"
            )

        initial_prompt, history = session

        recent = history[1:][-20:]

        if recent and recent[0]["role"] == "assistant":
            recent = recent[1:]

        messages = [
            {
                "role": "user",
                "content": initial_prompt,
            }
        ]

        messages.extend(recent)

        messages.append({
            "role": "user",
            "content": prompt,
        })

        answer = await self._request(
            messages,
            max_tokens=400,
        )

        messages.append({
            "role": "assistant",
            "content": answer,
        })

        async with self._db_lock:
            self._save_session(
                thread_id,
                initial_prompt,
                messages,
            )

        return CodexReply(
            text=answer,
            thread_id=thread_id,
        )
