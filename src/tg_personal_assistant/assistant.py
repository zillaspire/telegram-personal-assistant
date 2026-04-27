from __future__ import annotations

import asyncio
import hashlib
import random
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from telethon import TelegramClient, events
from telethon.sessions import StringSession

from tg_personal_assistant.codex_client import CodexCliClient
from tg_personal_assistant.config import AppSettings
from tg_personal_assistant.state import StateStore
from tg_personal_assistant.voice import (
    EdgeVoiceNoteSynthesizer,
    GroqVoiceTranscriber,
    convert_audio_for_transcription,
    format_incoming_voice_for_context,
    format_outgoing_voice_for_context,
    is_voice_message,
)

SYNC_BATCH_LIMIT = 40
VOICE_MEMORY_LIMIT = 400


def normalize_text(text: str) -> str:
    value = text.casefold()
    value = re.sub(r"[^\w\s/]+", " ", value)
    return " ".join(value.split())


def contains_phrase_trigger(text: str, triggers: list[str]) -> bool:
    normalized_text = normalize_text(text)
    return any(normalize_text(trigger) in normalized_text for trigger in triggers)


def build_reply_prompt(base_prompt: str, latest_incoming_text: str) -> str:
    latest = latest_incoming_text.strip()
    return (
        f"{base_prompt.strip()}\n\n"
        "Главный приоритет — последнее входящее сообщение. "
        "Отвечай именно на него, а более старую переписку используй как фон для общего контекста, памяти и тона.\n"
        "Сохраняй связность с предыдущим диалогом, но не уводи ответ в старую тему, если последнее сообщение её прямо не продолжает.\n"
        "Не подменяй предмет ответа старым конфликтом, шуткой или обсуждением, если в последнем сообщении другой фокус.\n"
        f"Последнее входящее сообщение: {latest}"
    )


def build_session_bootstrap_prompt(base_prompt: str, history: list[dict[str, Any]]) -> str:
    transcript_lines = []
    for item in history:
        role = "ТЫ" if item["role"] == "assistant" else "CONTACT"
        transcript_lines.append(f"{role}: {item['content']}")
    transcript = "\n".join(transcript_lines) if transcript_lines else "История пока пустая."
    return (
        "Это старт внутренней Codex-сессии для одного личного Telegram-диалога.\n"
        "Ниже есть системные инструкции и недавняя реальная история чата.\n"
        "Запомни контекст беседы и стиль общения, но прямо сейчас ответь только на последнее входящее сообщение от собеседника.\n"
        "Верни только один готовый текст ответа для Telegram, без анализа и служебных пояснений.\n\n"
        "Системные инструкции:\n"
        f"{base_prompt.strip()}\n\n"
        "Недавняя история диалога:\n"
        f"{transcript}"
    )


def build_session_turn_prompt(new_messages: list[dict[str, Any]]) -> str:
    transcript_lines = []
    latest_incoming = ""
    for item in new_messages:
        role = "ТЫ" if item["role"] == "assistant" else "CONTACT"
        transcript_lines.append(f"{role}: {item['content']}")
        if item["role"] == "user":
            latest_incoming = item["content"]
    transcript = "\n".join(transcript_lines)
    return (
        "Продолжай ту же внутреннюю сессию этого диалога.\n"
        "Ниже только новые реальные сообщения из Telegram с последней синхронизации.\n"
        "Используй уже накопленный контекст сессии, но ответь именно на последнее входящее сообщение.\n"
        "Верни только один готовый текст ответа для Telegram, без анализа и пояснений.\n\n"
        "Новые сообщения:\n"
        f"{transcript}\n\n"
        f"Последнее входящее сообщение от собеседника: {latest_incoming}"
    )


def prompt_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def build_runtime_settings_state(
    stored_settings: dict[str, Any],
    *,
    configured_prompt: str,
    phrase_triggers: list[str],
    model: str,
) -> dict[str, Any]:
    persisted_prompt = str(stored_settings.get("system_prompt", "")).strip()
    system_prompt = persisted_prompt or configured_prompt
    return {
        "system_prompt": system_prompt,
        "phrase_triggers": list(phrase_triggers),
        "model": model,
        "prompt_fingerprint": prompt_fingerprint(system_prompt),
    }


def has_newer_outgoing_than_trigger(
    trigger_message_id: int,
    latest_message_id: int | None,
    *,
    latest_message_is_outgoing: bool,
) -> bool:
    if latest_message_id is None or latest_message_id <= trigger_message_id:
        return False
    return latest_message_is_outgoing


def estimate_typing_window_seconds(reply_text: str) -> float:
    text_length = len(reply_text.strip())
    if text_length <= 32:
        return 1.0
    if text_length <= 120:
        return 1.6
    return 2.2


class PersonalAssistantRuntime:
    def __init__(
        self,
        settings: AppSettings,
        store: StateStore,
        llm_client: CodexCliClient,
        voice_transcriber: GroqVoiceTranscriber | None = None,
        voice_synthesizer: EdgeVoiceNoteSynthesizer | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._llm_client = llm_client
        self._voice_transcriber = voice_transcriber
        self._voice_synthesizer = voice_synthesizer
        self._control_bot = None
        self._client = TelegramClient(
            StringSession(settings.tg_user_session),
            settings.tg_api_id,
            settings.tg_api_hash,
        )
        self._shutdown_event = asyncio.Event()
        self._pending_reply_task: asyncio.Task[None] | None = None
        self._handler_registered = False

    async def start(self) -> None:
        await self._client.connect()
        if not await self._client.is_user_authorized():
            raise RuntimeError("Telethon session is not authorized")
        me = await self._client.get_me()
        owner_id = self._settings.owner_telegram_id or int(me.id)
        settings_state = build_runtime_settings_state(
            self._store.get_json("settings", default={}),
            configured_prompt=self._settings.system_prompt,
            phrase_triggers=self._settings.phrase_triggers,
            model=self._settings.model,
        )
        self._store.set_json(
            "runtime",
            {
                **self._store.get_json(
                    "runtime",
                    default={"mode": self._settings.mode, "paused_until": None, "pause_type": None},
                ),
                "owner_telegram_id": owner_id,
            },
        )
        self._store.set_json(
            "settings",
            settings_state,
        )
        self._store.set_json(
            "message_state",
            self._store.get_json(
                "message_state",
                default={
                    "last_incoming_message_id": None,
                    "last_auto_replied_incoming_message_id": None,
                    "last_outgoing_message_id": None,
                    "awaiting_prompt_update": False,
                    "awaiting_first_message_draft": False,
                },
            ),
        )
        self._store.set_json(
            "codex_session",
            self._store.get_json(
                "codex_session",
                default={
                    "thread_id": None,
                    "prompt_fingerprint": settings_state["prompt_fingerprint"],
                    "reply_count": 0,
                    "last_synced_chat_message_id": None,
                    "started_at": None,
                    "last_used_at": None,
                },
            ),
        )
        if not self._handler_registered:
            self._client.add_event_handler(
                self._on_new_message,
                events.NewMessage(incoming=True, chats=[self._settings.target_chat_id]),
            )
            self._handler_registered = True

    async def stop(self) -> None:
        if self._pending_reply_task and not self._pending_reply_task.done():
            self._pending_reply_task.cancel()
            try:
                await self._pending_reply_task
            except asyncio.CancelledError:
                pass
        await self._llm_client.close()
        await self._client.disconnect()
        self._shutdown_event.set()

    def attach_control_bot(self, control_bot: Any) -> None:
        self._control_bot = control_bot

    async def wait_forever(self) -> None:
        await self._shutdown_event.wait()

    async def _on_new_message(self, event: events.NewMessage.Event) -> None:
        message = event.message
        text = (message.raw_text or "").strip()
        is_text = bool(text)
        is_voice = is_voice_message(message)
        payload = {
            "incoming_message_id": message.id,
            "text_preview": text[:300],
            "is_text": is_text,
            "is_voice": is_voice,
            "chat_id": getattr(message.peer_id, "user_id", None)
            or getattr(message.peer_id, "chat_id", None)
            or getattr(message.peer_id, "channel_id", None),
        }
        self._store.log_event("incoming_message", payload)
        message_state = self._store.get_json("message_state")
        message_state["last_incoming_message_id"] = message.id
        self._store.set_json("message_state", message_state)

        if is_voice:
            if not self._settings.voice_replies_enabled:
                await self._send_alert("manual_review", "Voice message received, but voice replies are disabled.")
                return
            if not self._should_auto_reply():
                return
            if self._pending_reply_task and not self._pending_reply_task.done():
                self._pending_reply_task.cancel()
            self._pending_reply_task = asyncio.create_task(
                self._reply_with_voice_after_delay(
                    trigger_message_id=message.id,
                    source_message=message,
                )
            )
            return

        if not is_text:
            await self._send_alert("manual_review", "Non-text message received; no auto-reply.")
            return

        if self._is_slash_stop(text):
            await self.pause_until_resume("slash_stop")
            await self._send_alert("slash_stop", f"Received /stop in target chat: {text[:200]}")
            return

        if contains_phrase_trigger(text, self._settings.phrase_triggers):
            await self._send_alert("escalation_phrase", f"Escalation phrase detected: {text[:200]}")
            return

        if not self._should_auto_reply():
            return

        if self._pending_reply_task and not self._pending_reply_task.done():
            self._pending_reply_task.cancel()

        self._pending_reply_task = asyncio.create_task(
            self._reply_after_delay(trigger_message_id=message.id)
        )

    async def _reply_after_delay(self, trigger_message_id: int) -> None:
        delay = random.randint(
            self._settings.auto_reply_min_delay_sec,
            self._settings.auto_reply_max_delay_sec,
        )
        try:
            await asyncio.sleep(delay)
            message_state = self._store.get_json("message_state")
            if message_state.get("last_incoming_message_id") != trigger_message_id:
                self._store.log_event(
                    "reply_cancelled",
                    {
                        "trigger_message_id": trigger_message_id,
                        "reason": "superseded_by_newer_message",
                    },
                )
                return
            if message_state.get("last_auto_replied_incoming_message_id") == trigger_message_id:
                return
            if not self._should_auto_reply():
                return
            if await self._cancel_if_owner_replied(
                trigger_message_id,
                stage="before_generation",
            ):
                return
            typing_started_at = asyncio.get_running_loop().time()
            async with self._client.action(self._settings.target_chat_id, "typing"):
                history = await self._collect_history()
                reply = await self._generate_auto_reply(history)
                if await self._cancel_if_owner_replied(
                    trigger_message_id,
                    stage="before_send",
                ):
                    return
                remaining_typing_window = estimate_typing_window_seconds(reply) - (
                    asyncio.get_running_loop().time() - typing_started_at
                )
                if remaining_typing_window > 0:
                    await asyncio.sleep(remaining_typing_window)
                sent = await self._client.send_message(self._settings.target_chat_id, reply)
            self._finalize_sent_reply(
                trigger_message_id=trigger_message_id,
                sent_message=sent,
                event_kind="auto_reply_sent",
                text_preview=reply,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if "reply" in locals():
                session_state = self._get_codex_session_state()
                session_state["thread_id"] = None
                self._store.set_json("codex_session", session_state)
            self._store.log_event(
                "send_error",
                {"trigger_message_id": trigger_message_id, "error": str(exc)},
            )
            await self._send_alert("send_error", f"Failed to auto-reply: {exc}")

    async def _reply_with_voice_after_delay(
        self,
        *,
        trigger_message_id: int,
        source_message: Any,
    ) -> None:
        delay = random.randint(
            self._settings.auto_reply_min_delay_sec,
            self._settings.auto_reply_max_delay_sec,
        )
        try:
            await asyncio.sleep(delay)
            message_state = self._store.get_json("message_state")
            if message_state.get("last_incoming_message_id") != trigger_message_id:
                self._store.log_event(
                    "reply_cancelled",
                    {
                        "trigger_message_id": trigger_message_id,
                        "reason": "superseded_by_newer_message",
                    },
                )
                return
            if message_state.get("last_auto_replied_incoming_message_id") == trigger_message_id:
                return
            if not self._should_auto_reply():
                return
            if await self._cancel_if_owner_replied(
                trigger_message_id,
                stage="before_generation",
            ):
                return
            if self._voice_transcriber is None:
                await self._send_alert(
                    "manual_review",
                    "Voice message received, but GROQ_API_KEY is not configured for transcription.",
                )
                return
            if self._voice_synthesizer is None:
                await self._send_alert(
                    "manual_review",
                    "Voice message received, but voice synthesizer is not configured.",
                )
                return

            async with asyncio.timeout(180):
                with tempfile.TemporaryDirectory(prefix="tg-personal-voice-") as temp_dir:
                    temp_path = Path(temp_dir)
                    downloaded_path = await self._client.download_media(
                        source_message,
                        file=str(temp_path / "incoming-voice"),
                    )
                    if not downloaded_path:
                        raise RuntimeError("Telethon could not download incoming voice message")
                    prepared_audio_path = await convert_audio_for_transcription(downloaded_path)
                    transcript = await self._voice_transcriber.transcribe(prepared_audio_path)
                    context_text = format_incoming_voice_for_context(transcript)
                    self._remember_voice_memory(
                        message_id=trigger_message_id,
                        text=context_text,
                        kind="incoming_voice",
                    )
                    self._store.log_event(
                        "voice_transcribed",
                        {
                            "trigger_message_id": trigger_message_id,
                            "transcript_preview": transcript[:300],
                        },
                    )
                    async with self._client.action(self._settings.target_chat_id, "record-voice"):
                        history = await self._collect_history()
                        reply_text = await self._generate_auto_reply(history)
                        if await self._cancel_if_owner_replied(
                            trigger_message_id,
                            stage="before_send",
                        ):
                            return
                        voice_path = await self._voice_synthesizer.synthesize_voice_note(
                            reply_text,
                            output_dir=temp_path,
                        )
                        sent = await self._client.send_file(
                            self._settings.target_chat_id,
                            voice_path,
                            voice_note=True,
                        )
                    self._remember_voice_memory(
                        message_id=getattr(sent, "id", 0),
                        text=format_outgoing_voice_for_context(reply_text),
                        kind="outgoing_voice",
                    )
                    self._finalize_sent_reply(
                        trigger_message_id=trigger_message_id,
                        sent_message=sent,
                        event_kind="auto_voice_reply_sent",
                        text_preview=reply_text,
                    )
        except TimeoutError as exc:
            if "reply_text" in locals():
                if await self._send_voice_fallback_text(
                    trigger_message_id=trigger_message_id,
                    reply_text=reply_text,
                    reason=f"Voice pipeline timed out: {exc}",
                ):
                    return
            self._store.log_event(
                "voice_error",
                {"trigger_message_id": trigger_message_id, "error": str(exc)},
            )
            await self._send_alert("voice_error", "Voice pipeline timed out; no auto-reply was sent.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if "reply_text" in locals():
                if await self._send_voice_fallback_text(
                    trigger_message_id=trigger_message_id,
                    reply_text=reply_text,
                    reason=f"Voice reply failed, sent text fallback: {exc}",
                ):
                    return
            self._store.log_event(
                "voice_error",
                {"trigger_message_id": trigger_message_id, "error": str(exc)},
            )
            await self._send_alert("voice_error", f"Failed to process voice message: {exc}")

    async def _send_voice_fallback_text(
        self,
        *,
        trigger_message_id: int,
        reply_text: str,
        reason: str,
    ) -> bool:
        if await self._cancel_if_owner_replied(
            trigger_message_id,
            stage="before_send",
        ):
            return False
        async with self._client.action(self._settings.target_chat_id, "typing"):
            sent = await self._client.send_message(
                self._settings.target_chat_id,
                reply_text,
            )
        self._finalize_sent_reply(
            trigger_message_id=trigger_message_id,
            sent_message=sent,
            event_kind="auto_voice_fallback_text_sent",
            text_preview=reply_text,
        )
        await self._send_alert("voice_fallback", reason)
        return True

    async def _generate_auto_reply(
        self,
        history: list[dict[str, Any]],
    ) -> str:
        session_state = self._get_codex_session_state()
        current_prompt = self.get_prompt()
        current_prompt_fingerprint = prompt_fingerprint(current_prompt)
        if session_state.get("prompt_fingerprint") != current_prompt_fingerprint:
            await self.reset_codex_session()
            session_state = self._get_codex_session_state()
            session_state["prompt_fingerprint"] = current_prompt_fingerprint
            self._store.set_json("codex_session", session_state)

        thread_id = session_state.get("thread_id")
        if not thread_id:
            bootstrap_prompt = build_session_bootstrap_prompt(current_prompt, history)
            result = await self._llm_client.start_session(bootstrap_prompt)
            session_state["thread_id"] = result.thread_id
            session_state["reply_count"] = 1
            session_state["started_at"] = datetime.now(timezone.utc).isoformat()
            session_state["last_used_at"] = session_state["started_at"]
            self._store.set_json("codex_session", session_state)
            self._store.log_event(
                "session_started",
                {
                    "thread_id": result.thread_id,
                    "reply_count": session_state["reply_count"],
                },
            )
            return result.text

        new_messages = await self._collect_incremental_history(
            after_message_id=session_state.get("last_synced_chat_message_id"),
            limit=max(SYNC_BATCH_LIMIT, self._settings.history_messages_limit),
        )
        if not new_messages:
            new_messages = history[-1:]
        turn_prompt = build_session_turn_prompt(new_messages)
        try:
            result = await self._llm_client.resume_session(thread_id, turn_prompt)
        except RuntimeError as exc:
            session_state["thread_id"] = None
            self._store.set_json("codex_session", session_state)
            self._store.log_event(
                "session_resume_failed",
                {
                    "thread_id": thread_id,
                    "error": str(exc),
                },
            )
            bootstrap_prompt = build_session_bootstrap_prompt(current_prompt, history)
            result = await self._llm_client.start_session(bootstrap_prompt)
            session_state["thread_id"] = result.thread_id
            session_state["reply_count"] = 1
            session_state["started_at"] = datetime.now(timezone.utc).isoformat()
            session_state["last_used_at"] = session_state["started_at"]
            self._store.set_json("codex_session", session_state)
            self._store.log_event(
                "session_started",
                {
                    "thread_id": result.thread_id,
                    "reply_count": session_state["reply_count"],
                    "reason": "resume_failed_restarted",
                },
            )
            return result.text

        session_state["thread_id"] = result.thread_id or thread_id
        session_state["reply_count"] = int(session_state.get("reply_count", 0) or 0) + 1
        session_state["last_used_at"] = datetime.now(timezone.utc).isoformat()
        self._store.set_json("codex_session", session_state)
        return result.text

    async def _cancel_if_owner_replied(
        self,
        trigger_message_id: int,
        *,
        stage: str,
    ) -> bool:
        latest_message = await self._get_latest_chat_message()
        latest_message_id = getattr(latest_message, "id", None)
        if not has_newer_outgoing_than_trigger(
            trigger_message_id,
            latest_message_id,
            latest_message_is_outgoing=bool(getattr(latest_message, "out", False)),
        ):
            return False
        reason = (
            "owner_replied_after_trigger"
            if stage == "before_generation"
            else "owner_replied_while_generating"
        )
        self._store.log_event(
            "reply_cancelled",
            {
                "trigger_message_id": trigger_message_id,
                "latest_message_id": latest_message_id,
                "reason": reason,
            },
        )
        return True

    async def _get_latest_chat_message(self) -> Any | None:
        messages = await self._client.get_messages(
            self._settings.target_chat_id,
            limit=1,
        )
        if messages is None:
            return None
        try:
            return messages[0]
        except (IndexError, KeyError, TypeError):
            return messages

    def _finalize_sent_reply(
        self,
        *,
        trigger_message_id: int,
        sent_message: Any,
        event_kind: str,
        text_preview: str,
    ) -> None:
        message_state = self._store.get_json("message_state")
        message_state["last_auto_replied_incoming_message_id"] = trigger_message_id
        message_state["last_outgoing_message_id"] = getattr(sent_message, "id", None)
        self._store.set_json("message_state", message_state)

        session_state = self._get_codex_session_state()
        session_state["last_synced_chat_message_id"] = getattr(sent_message, "id", None)
        session_state["last_used_at"] = datetime.now(timezone.utc).isoformat()
        self._store.set_json("codex_session", session_state)

        self._store.log_event(
            event_kind,
            {
                "trigger_message_id": trigger_message_id,
                "outgoing_message_id": getattr(sent_message, "id", None),
                "text_preview": text_preview[:300],
            },
        )

    def _remember_voice_memory(
        self,
        *,
        message_id: int,
        text: str,
        kind: str,
    ) -> None:
        voice_memory = self._store.get_json("voice_memory", default={})
        voice_memory[str(message_id)] = {
            "kind": kind,
            "text": text,
        }
        if len(voice_memory) > VOICE_MEMORY_LIMIT:
            stale_keys = sorted(voice_memory, key=lambda value: int(value))[:-VOICE_MEMORY_LIMIT]
            for key in stale_keys:
                voice_memory.pop(key, None)
        self._store.set_json("voice_memory", voice_memory)

    def _resolve_message_content(
        self,
        message: Any,
        *,
        voice_memory: dict[str, dict[str, str]],
    ) -> str:
        text = (getattr(message, "raw_text", "") or "").strip()
        if text:
            return text
        if is_voice_message(message):
            return str(voice_memory.get(str(getattr(message, "id", "")), {}).get("text", "")).strip()
        return ""

    async def _collect_history(self, limit: int | None = None) -> list[dict[str, Any]]:
        messages = await self._client.get_messages(
            self._settings.target_chat_id,
            limit=limit or self._settings.history_messages_limit,
        )
        voice_memory = self._store.get_json("voice_memory", default={})
        result: list[dict[str, Any]] = []
        for item in reversed(messages):
            text = self._resolve_message_content(item, voice_memory=voice_memory)
            if not text:
                continue
            role = "assistant" if item.out else "user"
            result.append({"message_id": item.id, "role": role, "content": text})
        return result

    async def _collect_incremental_history(
        self,
        *,
        after_message_id: int | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        if not after_message_id:
            return await self._collect_history(limit=limit)
        messages = await self._client.get_messages(
            self._settings.target_chat_id,
            limit=limit,
            min_id=after_message_id,
        )
        voice_memory = self._store.get_json("voice_memory", default={})
        result: list[dict[str, Any]] = []
        for item in reversed(messages):
            text = self._resolve_message_content(item, voice_memory=voice_memory)
            if not text:
                continue
            result.append(
                {
                    "message_id": item.id,
                    "role": "assistant" if item.out else "user",
                    "content": text,
                }
            )
        return result

    async def _send_alert(self, kind: str, text: str) -> None:
        self._store.log_event("alert", {"kind": kind, "text": text})
        if self._control_bot is not None:
            await self._control_bot.send_owner_message(f"[{kind}] {text}")

    def _should_auto_reply(self) -> bool:
        runtime = self._store.get_json("runtime")
        mode = runtime.get("mode", self._settings.mode)
        if mode != "auto":
            return False
        paused_until = runtime.get("paused_until")
        pause_type = runtime.get("pause_type")
        if pause_type == "until_resume":
            return False
        if paused_until:
            try:
                until = datetime.fromisoformat(paused_until)
            except ValueError:
                return True
            if until > datetime.now(timezone.utc):
                return False
        return True

    def _is_slash_stop(self, text: str) -> bool:
        return normalize_text(text).startswith("/stop")

    async def set_mode(self, mode: str) -> None:
        runtime = self._store.get_json("runtime")
        runtime["mode"] = mode
        if mode == "auto":
            runtime["paused_until"] = None
            runtime["pause_type"] = None
        self._store.set_json("runtime", runtime)

    async def pause_for(self, minutes: int) -> None:
        runtime = self._store.get_json("runtime")
        runtime["mode"] = "auto"
        runtime["pause_type"] = "timed"
        runtime["paused_until"] = (
            datetime.now(timezone.utc) + timedelta(minutes=minutes)
        ).isoformat()
        self._store.set_json("runtime", runtime)

    async def pause_until_resume(self, reason: str) -> None:
        runtime = self._store.get_json("runtime")
        runtime["mode"] = "auto"
        runtime["pause_type"] = "until_resume"
        runtime["paused_until"] = None
        runtime["stop_reason"] = reason
        self._store.set_json("runtime", runtime)

    async def resume(self) -> None:
        runtime = self._store.get_json("runtime")
        runtime["mode"] = "auto"
        runtime["pause_type"] = None
        runtime["paused_until"] = None
        runtime["stop_reason"] = None
        self._store.set_json("runtime", runtime)

    def get_prompt(self) -> str:
        settings = self._store.get_json("settings")
        return settings.get("system_prompt", self._settings.system_prompt)

    def _get_codex_session_state(self) -> dict[str, Any]:
        return self._store.get_json(
            "codex_session",
            default={
                "thread_id": None,
                "prompt_fingerprint": prompt_fingerprint(self.get_prompt()),
                "reply_count": 0,
                "last_synced_chat_message_id": None,
                "started_at": None,
                "last_used_at": None,
            },
        )

    async def reset_codex_session(self) -> None:
        session_state = self._get_codex_session_state()
        session_state["thread_id"] = None
        session_state["reply_count"] = 0
        session_state["started_at"] = None
        session_state["last_used_at"] = None
        session_state["prompt_fingerprint"] = prompt_fingerprint(self.get_prompt())
        self._store.set_json("codex_session", session_state)

    def describe_codex_session(self) -> str:
        session_state = self._get_codex_session_state()
        thread_id = session_state.get("thread_id")
        if not thread_id:
            return "Внутренняя Codex-сессия сейчас не активна."
        return (
            "Внутренняя Codex-сессия активна.\n"
            f"thread_id: {thread_id}\n"
            f"reply_count: {session_state.get('reply_count', 0)}\n"
            f"last_synced_chat_message_id: {session_state.get('last_synced_chat_message_id')}\n"
            f"started_at: {session_state.get('started_at')}\n"
            f"last_used_at: {session_state.get('last_used_at')}"
        )

    def mark_prompt_update_pending(self) -> None:
        state = self._store.get_json("message_state")
        state["awaiting_prompt_update"] = True
        state["awaiting_first_message_draft"] = False
        self._store.set_json("message_state", state)

    def mark_first_message_draft_pending(self) -> None:
        state = self._store.get_json("message_state")
        state["awaiting_first_message_draft"] = True
        state["awaiting_prompt_update"] = False
        self._store.set_json("message_state", state)

    def clear_pending_owner_input(self) -> None:
        state = self._store.get_json("message_state")
        state["awaiting_first_message_draft"] = False
        state["awaiting_prompt_update"] = False
        self._store.set_json("message_state", state)

    async def handle_owner_free_text(self, text: str) -> tuple[bool, str]:
        if self.consume_prompt_update(text):
            return True, "Системный промпт обновлён."
        first_message_draft = await self.generate_first_message_draft(text)
        if first_message_draft is not None:
            return True, first_message_draft
        return False, ""

    def consume_prompt_update(self, text: str) -> bool:
        state = self._store.get_json("message_state")
        if not state.get("awaiting_prompt_update"):
            return False
        settings = self._store.get_json("settings")
        settings["system_prompt"] = text
        settings["prompt_fingerprint"] = prompt_fingerprint(text)
        self._store.set_json("settings", settings)
        state["awaiting_prompt_update"] = False
        self._store.set_json("message_state", state)
        session_state = self._get_codex_session_state()
        session_state["thread_id"] = None
        session_state["reply_count"] = 0
        session_state["started_at"] = None
        session_state["last_used_at"] = None
        session_state["prompt_fingerprint"] = settings["prompt_fingerprint"]
        self._store.set_json("codex_session", session_state)
        self._store.log_event("prompt_updated", {"text_preview": text[:300]})
        return True

    async def generate_first_message_draft(self, owner_instruction: str) -> str | None:
        state = self._store.get_json("message_state")
        if not state.get("awaiting_first_message_draft"):
            return None
        history = await self._collect_history()
        compact_history = history[-4:]
        system_prompt = (
            f"{self.get_prompt()}\n\n"
            "Сейчас нужна не реакция на входящее, а черновик первого сообщения, которое владелец сам хочет отправить первым. "
            "Верни только один готовый текст сообщения для отправки. "
            "Пиши коротко, естественно и без пояснений, кавычек и вариантов."
        )
        owner_request = {
            "role": "user",
            "content": (
                "Сделай черновик первого сообщения по задаче владельца.\n"
                f"Задача: {owner_instruction}"
            ),
        }
        try:
            draft = await self._llm_client.generate_reply(
                system_prompt,
                [*compact_history, owner_request],
                temperature=0.7,
                max_tokens=700,
            )
        except ValueError as exc:
            if "empty reply" not in str(exc).lower():
                raise
            retry_prompt = (
                "Напиши только итоговый текст первого сообщения для девушки. "
                "Без анализа, без пояснений, без вариантов. "
                "Одна короткая готовая реплика."
            )
            draft = await self._llm_client.generate_reply(
                retry_prompt,
                [owner_request],
                temperature=0.6,
                max_tokens=220,
            )
        state["awaiting_first_message_draft"] = False
        self._store.set_json("message_state", state)
        self._store.log_event(
            "first_message_draft_generated",
            {
                "owner_instruction": owner_instruction[:300],
                "draft_preview": draft[:300],
            },
        )
        return (
            "Черновик первого сообщения:\n\n"
            f"{draft}"
        )

    def describe_status(self) -> str:
        runtime = self._store.get_json("runtime")
        mode = runtime.get("mode", self._settings.mode)
        pause_type = runtime.get("pause_type")
        paused_until = runtime.get("paused_until")
        if mode == "off":
            state_label = "Выключен"
        elif pause_type == "until_resume":
            state_label = "Пауза до /resume"
        elif pause_type == "timed" and paused_until:
            state_label = f"Пауза до {paused_until}"
        else:
            state_label = "Автоответ включён"
        message_state = self._store.get_json("message_state")
        session_state = self._get_codex_session_state()
        thread_id = session_state.get("thread_id")
        status_lines = [
            f"Состояние: {state_label}",
            f"Модель: {self._settings.model}",
            f"Целевой чат ID: {self._settings.target_chat_id}",
            f"Owner ID: {runtime.get('owner_telegram_id')}",
            f"Voice replies: {'включены' if self._settings.voice_replies_enabled else 'выключены'}",
            f"Внутренняя сессия: {'активна' if thread_id else 'нет'}",
            f"thread_id: {thread_id or '-'}",
            f"Ответов в текущей сессии: {session_state.get('reply_count', 0)}",
            f"Ждёт новый промпт: {'да' if message_state.get('awaiting_prompt_update') else 'нет'}",
            f"Ждёт задачу для первого черновика: {'да' if message_state.get('awaiting_first_message_draft') else 'нет'}",
        ]
        return "\n".join(status_lines)

    def describe_last_event(self) -> str:
        event = self._store.get_last_event()
        if not event:
            return "Пока событий нет."
        payload = event["payload"]
        lines = [
            f"Время: {event['created_at']}",
            f"Тип: {event['kind']}",
        ]
        if isinstance(payload, dict):
            for key, value in payload.items():
                lines.append(f"{key}: {value}")
        else:
            lines.append(str(payload))
        return "\n".join(lines)
