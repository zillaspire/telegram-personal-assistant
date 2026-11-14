
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


DEFAULT_TRIGGER_PHRASES = [
    "позови человека",
    "хочу с тобой лично",
    "это ты или бот?",
]


@dataclass(slots=True)
class AppSettings:
    config_path: Path
    secrets_path: Path
    sqlite_db_path: Path
    mode: str
    model: str
    codex_command: str
    codex_timeout_sec: int
    target_chat_id: int
    owner_telegram_id: int | None
    history_messages_limit: int
    auto_reply_min_delay_sec: int
    auto_reply_max_delay_sec: int
    voice_replies_enabled: bool
    groq_stt_model: str
    tts_voice: str
    phrase_triggers: list[str]
    system_prompt: str
    tg_api_id: int
    tg_api_hash: str
    tg_user_session: str
    control_bot_token: str
    groq_api_key: str | None


def _require_int(source: dict[str, Any], key: str) -> int:
    raw = source.get(key)
    if raw in (None, ""):
        raise ValueError(f"Missing required integer setting: {key}")
    return int(raw)


def _require_str(source: dict[str, Any], key: str) -> str:
    raw = source.get(key)
    if raw in (None, ""):
        raise ValueError(f"Missing required string setting: {key}")
    return str(raw).strip()


def _optional_str(source: dict[str, Any], key: str) -> str | None:
    raw = source.get(key)
    if raw in (None, ""):
        return None
    value = str(raw).strip()
    return value or None


def load_settings(
    config_path: str | Path = "config.toml",
    secrets_path: str | Path = "secrets.env",
) -> AppSettings:
    config_file = Path(config_path).expanduser().resolve()
    secrets_file = Path(secrets_path).expanduser().resolve()

    # Read configuration from file if available.
    if config_file.exists():
        with config_file.open("rb") as fh:
            data = tomllib.load(fh)
    else:
        # Allow cloud deployment without config.toml.
        data = {
            "assistant": {
                "mode": os.getenv("ASSISTANT_MODE", "auto"),
                "model": "claude-sonnet-5-5",
                "target_chat_id": os.getenv("TARGET_CHAT_ID", "0"),
                "owner_telegram_id": os.getenv(
                    "OWNER_TELEGRAM_ID", "0"
                ),
                "history_messages_limit": 12,
                "auto_reply_min_delay_sec": 1,
                "auto_reply_max_delay_sec": 2,
                "voice_replies_enabled": False,
                "system_prompt": os.getenv(
                    "AI_PERSONALITY",
                    (
                        "Ты дружелюбный ИИ-собеседник Telegram. "
                        "Отвечай естественно и коротко, "
                        "учитывай контекст и настроение человека. "
                        "Не выдумывай воспоминания. "
                        "Не скрывай, что ты ИИ."
                    ),
                ),
            },
            "paths": {
                "sqlite_db_path": os.getenv(
                    "SQLITE_DB_PATH",
                    "./data/personal-assistant.sqlite3",
                )
            },
        }

    assistant = data.get("assistant", {})
    paths = data.get("paths", {})

    # Load optional local secrets.
    secrets = {}
    if secrets_file.exists():
        secrets.update({
            k: v
            for k, v in dotenv_values(secrets_file).items()
            if v is not None
        })

    # Cloud environment variables take priority.
    for key in (
        "TG_API_ID",
        "TG_API_HASH",
        "TG_USER_SESSION",
        "CONTROL_BOT_TOKEN",
        "GROQ_API_KEY",
    ):
        value = os.getenv(key)
        if value:
            secrets[key] = value

    target_chat_id = int(
        assistant.get("target_chat_id", 0) or 0
    )

    if target_chat_id == 0:
        raise ValueError(
            "Set TARGET_CHAT_ID in Blitz.cloud"
        )

    owner_raw = assistant.get("owner_telegram_id", 0) or 0
    owner_telegram_id = (
        int(owner_raw) if int(owner_raw) != 0 else None
    )

    min_delay = int(
        assistant.get("auto_reply_min_delay_sec", 1)
    )
    max_delay = int(
        assistant.get("auto_reply_max_delay_sec", 2)
    )

    if (
        min_delay < 0
        or max_delay < 0
        or min_delay > max_delay
    ):
        raise ValueError("Invalid auto reply delay")

    sqlite_raw = str(
        paths.get(
            "sqlite_db_path",
            "./data/personal-assistant.sqlite3",
        )
    )

    sqlite_db_path = (
        config_file.parent / sqlite_raw
    ).resolve()

    return AppSettings(
        config_path=config_file,
        secrets_path=secrets_file,
        sqlite_db_path=sqlite_db_path,
        mode=str(
            assistant.get("mode", "auto")
        ).strip().lower(),
        model=str(
            assistant.get("model", "claude-sonnet-5-5")
        ).strip(),
        codex_command="codex",
        codex_timeout_sec=180,
        target_chat_id=target_chat_id,
        owner_telegram_id=owner_telegram_id,
        history_messages_limit=max(
            4,
            int(
                assistant.get(
                    "history_messages_limit", 12
                )
            ),
        ),
        auto_reply_min_delay_sec=min_delay,
        auto_reply_max_delay_sec=max_delay,
        voice_replies_enabled=bool(
            assistant.get(
                "voice_replies_enabled", False
            )
        ),
        groq_stt_model=str(
            assistant.get(
                "groq_stt_model",
                "whisper-large-v3-turbo",
            )
        ).strip(),
        tts_voice=str(
            assistant.get(
                "tts_voice",
                "ru-RU-DmitryNeural",
            )
        ).strip(),
        phrase_triggers=[
            str(item).strip()
            for item in assistant.get(
                "phrase_triggers",
                DEFAULT_TRIGGER_PHRASES,
            )
            if str(item).strip()
        ],
        system_prompt=_require_str(
            assistant, "system_prompt"
        ),
        tg_api_id=_require_int(
            secrets, "TG_API_ID"
        ),
        tg_api_hash=_require_str(
            secrets, "TG_API_HASH"
        ),
        tg_user_session=_require_str(
            secrets, "TG_USER_SESSION"
        ),
        control_bot_token=_require_str(
            secrets, "CONTROL_BOT_TOKEN"
        ),
        groq_api_key=_optional_str(
            secrets, "GROQ_API_KEY"
        ),
    )
