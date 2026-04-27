from __future__ import annotations

import argparse
import asyncio
import signal

from tg_personal_assistant.assistant import PersonalAssistantRuntime
from tg_personal_assistant.codex_client import CodexCliClient
from tg_personal_assistant.config import load_settings
from tg_personal_assistant.control_bot import ControlBotManager
from tg_personal_assistant.state import StateStore
from tg_personal_assistant.voice import EdgeVoiceNoteSynthesizer, GroqVoiceTranscriber


async def _run(config_path: str, secrets_path: str) -> None:
    settings = load_settings(config_path=config_path, secrets_path=secrets_path)
    store = StateStore(settings.sqlite_db_path)
    llm_client = CodexCliClient(
        model=settings.model,
        codex_command=settings.codex_command,
        timeout_sec=settings.codex_timeout_sec,
    )
    voice_transcriber = None
    if settings.voice_replies_enabled and settings.groq_api_key:
        voice_transcriber = GroqVoiceTranscriber(
            api_key=settings.groq_api_key,
            model=settings.groq_stt_model,
        )
    voice_synthesizer = (
        EdgeVoiceNoteSynthesizer(voice=settings.tts_voice)
        if settings.voice_replies_enabled
        else None
    )
    assistant = PersonalAssistantRuntime(
        settings=settings,
        store=store,
        llm_client=llm_client,
        voice_transcriber=voice_transcriber,
        voice_synthesizer=voice_synthesizer,
    )
    await assistant.start()
    runtime = store.get_json("runtime")
    owner_id = int(runtime["owner_telegram_id"])
    control_bot = ControlBotManager(
        token=settings.control_bot_token,
        owner_id=owner_id,
        assistant=assistant,
    )
    assistant.attach_control_bot(control_bot)
    await control_bot.start()

    stop_event = asyncio.Event()

    def _handle_stop() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_stop)

    try:
        await stop_event.wait()
    finally:
        await control_bot.stop()
        await assistant.stop()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--secrets", default="secrets.env")
    args = parser.parse_args()
    asyncio.run(_run(config_path=args.config, secrets_path=args.secrets))
