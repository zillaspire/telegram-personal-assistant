from __future__ import annotations

import argparse
import asyncio
import re
import os
from pathlib import Path

from dotenv import dotenv_values
from telethon import TelegramClient
from telethon.sessions import StringSession


def _extract_env_or_commented_value(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    text = path.read_text(errors="ignore")
    patterns = [
        rf"^{re.escape(key)}=(.+)$",
        rf"^#\s*{re.escape(key)}=(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.MULTILINE)
        if match:
            return match.group(1).strip()
    return None


def _first_value(key: str, *, source_env: Path | None, cli_value: str | None = None) -> str:
    if cli_value:
        return cli_value.strip()
    env_value = os.environ.get(key)
    if env_value:
        return env_value.strip()
    if source_env is not None:
        return _extract_env_or_commented_value(source_env, key) or ""
    return ""


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    existing = {
        key: value
        for key, value in dotenv_values(path).items()
        if value is not None
    } if path.exists() else {}
    existing.update(values)
    existing.setdefault("CONTROL_BOT_TOKEN", "")
    existing.setdefault("GROQ_API_KEY", "")
    lines = [
        "# Generated locally. Do not commit.",
        f"TG_API_ID={existing.get('TG_API_ID', '')}",
        f"TG_API_HASH={existing.get('TG_API_HASH', '')}",
        f"TG_USER_SESSION={existing.get('TG_USER_SESSION', '')}",
        f"CONTROL_BOT_TOKEN={existing.get('CONTROL_BOT_TOKEN', '')}",
        f"GROQ_API_KEY={existing.get('GROQ_API_KEY', '')}",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def bootstrap_secrets_main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a local secrets.env file from CLI args, environment variables, or another .env file."
    )
    parser.add_argument("--output", default="secrets.env")
    parser.add_argument("--source-env", help="Optional .env file to read values from")
    parser.add_argument("--tg-api-id")
    parser.add_argument("--tg-api-hash")
    parser.add_argument("--tg-user-session")
    parser.add_argument("--control-bot-token")
    parser.add_argument("--groq-api-key")
    args = parser.parse_args()

    output = Path(args.output).expanduser().resolve()
    source_env = Path(args.source_env).expanduser().resolve() if args.source_env else None
    values = {
        "TG_API_ID": _first_value("TG_API_ID", source_env=source_env, cli_value=args.tg_api_id),
        "TG_API_HASH": _first_value("TG_API_HASH", source_env=source_env, cli_value=args.tg_api_hash),
        "TG_USER_SESSION": _first_value(
            "TG_USER_SESSION",
            source_env=source_env,
            cli_value=args.tg_user_session,
        ),
        "CONTROL_BOT_TOKEN": _first_value(
            "CONTROL_BOT_TOKEN",
            source_env=source_env,
            cli_value=args.control_bot_token,
        ),
        "GROQ_API_KEY": _first_value("GROQ_API_KEY", source_env=source_env, cli_value=args.groq_api_key),
    }
    missing = [key for key in ("TG_API_ID", "TG_API_HASH", "TG_USER_SESSION") if not values[key]]
    if missing:
        raise SystemExit(f"Failed to bootstrap secrets: missing {', '.join(missing)}")
    _write_env_file(output, values)
    print(f"Wrote {output}")
    if not values["CONTROL_BOT_TOKEN"]:
        print("CONTROL_BOT_TOKEN is empty; add a BotFather token before running the service.")
    if not values["GROQ_API_KEY"]:
        print("GROQ_API_KEY is empty; voice transcription will stay disabled until you add it.")


async def _list_dialogs_async(secrets_path: Path, limit: int) -> None:
    secrets = {
        key: value
        for key, value in dotenv_values(secrets_path).items()
        if value is not None
    }
    client = TelegramClient(
        StringSession(str(secrets["TG_USER_SESSION"])),
        int(secrets["TG_API_ID"]),
        str(secrets["TG_API_HASH"]),
    )
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise SystemExit("Telethon session is not authorized")
        dialogs = await client.get_dialogs(limit=limit)
        for dialog in dialogs:
            entity = dialog.entity
            username = getattr(entity, "username", None)
            print(
                f"id={dialog.id}\tname={dialog.name}\tusername={username or '-'}"
            )
    finally:
        await client.disconnect()


def list_dialogs_main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--secrets", default="secrets.env")
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()
    asyncio.run(_list_dialogs_async(Path(args.secrets).expanduser().resolve(), args.limit))
