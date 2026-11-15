import asyncio
from types import SimpleNamespace
from pathlib import Path

import tg_personal_assistant.assistant as assistant_module
from tg_personal_assistant.assistant import PersonalAssistantRuntime, prompt_fingerprint
from tg_personal_assistant.config import AppSettings
from tg_personal_assistant.state import StateStore


class FakeMessage:
    def __init__(self, message_id: int, *, out: bool, raw_text: str) -> None:
        self.id = message_id
        self.out = out
        self.raw_text = raw_text


class FakeActionContext:
    def __init__(self, client: "FakeClient", chat_id: int, action: str) -> None:
        self._client = client
        self._chat_id = chat_id
        self._action = action

    async def __aenter__(self) -> None:
        self._client.action_calls.append((self._chat_id, self._action))
        return None

    async def __aexit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001, ANN202
        del exc_type, exc, tb
        return False


class FakeClient:
    def __init__(self, latest_messages: list[FakeMessage] | None = None) -> None:
        self.latest_messages = list(latest_messages or [])
        self.sent_messages: list[FakeMessage] = []
        self.handlers: list[tuple[object, object]] = []
        self.action_calls: list[tuple[int, str]] = []

    async def connect(self) -> None:
        return None

    async def is_user_authorized(self) -> bool:
        return True

    async def get_me(self):  # noqa: ANN202
        return type("FakeMe", (), {"id": 123456789})()

    async def get_dialogs(self, limit=None):
        return [SimpleNamespace(id=987654321, entity=987654321, is_user=True)]

    async def get_input_entity(self, entity):
        return entity

    def add_event_handler(self, handler, event_builder) -> None:
        self.handlers.append((handler, event_builder))

    async def get_messages(self, chat_id: int, limit: int, min_id: int | None = None):  # noqa: ANN202
        del chat_id
        return [message for message in self.latest_messages if message.id > (min_id or 0)][:limit]

    async def send_message(self, chat_id: int, text: str) -> FakeMessage:
        next_id = max([message.id for message in self.latest_messages], default=0) + 1
        message = FakeMessage(next_id, out=True, raw_text=text)
        self.latest_messages.insert(0, message)
        self.sent_messages.append(message)
        return message

    def action(self, chat_id: int, action: str) -> FakeActionContext:
        return FakeActionContext(self, chat_id, action)

    async def disconnect(self) -> None:
        return None


class FakeLlmClient:
    async def close(self) -> None:
        return None


def make_settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        config_path=tmp_path / "config.toml",
        secrets_path=tmp_path / "secrets.env",
        sqlite_db_path=tmp_path / "data" / "state.sqlite3",
        mode="auto",
        model="gpt-5.4",
        codex_command="codex",
        codex_timeout_sec=180,
        target_chat_id=987654321,
        owner_telegram_id=123456789,
        history_messages_limit=12,
        auto_reply_min_delay_sec=0,
        auto_reply_max_delay_sec=0,
        voice_replies_enabled=True,
        groq_stt_model="whisper-large-v3-turbo",
        tts_voice="ru-RU-DmitryNeural",
        phrase_triggers=["позови человека"],
        system_prompt="Базовый системный промпт",
        tg_api_id=1,
        tg_api_hash="hash",
        tg_user_session="",
        control_bot_token="token",
        groq_api_key=None,
    )


def build_runtime(
    tmp_path: Path,
    monkeypatch,
    *,
    latest_messages: list[FakeMessage] | None = None,
) -> tuple[PersonalAssistantRuntime, StateStore, FakeClient]:
    fake_client = FakeClient(latest_messages=latest_messages)
    monkeypatch.setattr(assistant_module, "TelegramClient", lambda *args, **kwargs: fake_client)
    monkeypatch.setattr(assistant_module, "estimate_typing_window_seconds", lambda text: 0.0)
    store = StateStore(tmp_path / "data" / "state.sqlite3")
    runtime = PersonalAssistantRuntime(
        settings=make_settings(tmp_path),
        store=store,
        llm_client=FakeLlmClient(),
    )
    return runtime, store, fake_client


def seed_message_state(store: StateStore, *, last_incoming_message_id: int) -> None:
    state = store.get_json(
        "message_state",
        default={
            "last_incoming_message_id": None,
            "last_auto_replied_incoming_message_id": None,
            "last_outgoing_message_id": None,
            "awaiting_prompt_update": False,
            "awaiting_first_message_draft": False,
        },
    )
    state["last_incoming_message_id"] = last_incoming_message_id
    state["last_auto_replied_incoming_message_id"] = None
    state["last_outgoing_message_id"] = None
    store.set_json("message_state", state)


def test_start_preserves_prompt_saved_via_control_bot(tmp_path: Path, monkeypatch) -> None:
    runtime, store, _ = build_runtime(tmp_path, monkeypatch)
    saved_prompt = "Личный промпт, измененный через control bot"
    store.set_json(
        "settings",
        {
            "system_prompt": saved_prompt,
            "phrase_triggers": [],
            "model": "gpt-5.4",
            "prompt_fingerprint": prompt_fingerprint(saved_prompt),
        },
    )

    asyncio.run(runtime.start())

    settings_state = store.get_json("settings")
    session_state = store.get_json("codex_session")
    assert settings_state["system_prompt"] == saved_prompt
    assert settings_state["prompt_fingerprint"] == prompt_fingerprint(saved_prompt)
    assert session_state["prompt_fingerprint"] == prompt_fingerprint(saved_prompt)


def test_reply_after_delay_cancels_if_owner_already_replied(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime, store, fake_client = build_runtime(
        tmp_path,
        monkeypatch,
        latest_messages=[FakeMessage(11, out=True, raw_text="Я уже ответил сам")],
    )
    asyncio.run(runtime.start())
    seed_message_state(store, last_incoming_message_id=10)

    async def should_not_run(*args, **kwargs):  # noqa: ANN202
        raise AssertionError("auto-reply generation should be cancelled before it starts")

    runtime._collect_history = should_not_run  # type: ignore[method-assign]
    runtime._generate_auto_reply = should_not_run  # type: ignore[method-assign]

    asyncio.run(runtime._reply_after_delay(trigger_message_id=10))

    assert fake_client.sent_messages == []
    last_event = store.get_last_event()
    assert last_event is not None
    assert last_event["kind"] == "reply_cancelled"
    assert last_event["payload"]["reason"] == "owner_replied_after_trigger"


def test_reply_after_delay_cancels_if_owner_replied_while_generating(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime, store, fake_client = build_runtime(
        tmp_path,
        monkeypatch,
        latest_messages=[FakeMessage(10, out=False, raw_text="Зайка")],
    )
    asyncio.run(runtime.start())
    seed_message_state(store, last_incoming_message_id=10)

    async def fake_collect_history():  # noqa: ANN202
        return [{"message_id": 10, "role": "user", "content": "Зайка"}]

    async def fake_generate_reply(history):  # noqa: ANN202
        del history
        fake_client.latest_messages = [FakeMessage(11, out=True, raw_text="Я сам ответил")]
        return "Автоответ"

    runtime._collect_history = fake_collect_history  # type: ignore[method-assign]
    runtime._generate_auto_reply = fake_generate_reply  # type: ignore[method-assign]

    asyncio.run(runtime._reply_after_delay(trigger_message_id=10))

    assert fake_client.sent_messages == []
    last_event = store.get_last_event()
    assert last_event is not None
    assert last_event["kind"] == "reply_cancelled"
    assert last_event["payload"]["reason"] == "owner_replied_while_generating"


def test_reply_after_delay_keeps_persistent_session_after_many_replies(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime, store, fake_client = build_runtime(
        tmp_path,
        monkeypatch,
        latest_messages=[FakeMessage(10, out=False, raw_text="Зайка")],
    )
    asyncio.run(runtime.start())
    seed_message_state(store, last_incoming_message_id=10)
    store.set_json(
        "codex_session",
        {
            "thread_id": "thread-1",
            "prompt_fingerprint": prompt_fingerprint(runtime.get_prompt()),
            "reply_count": 24,
            "last_synced_chat_message_id": 9,
            "started_at": "2026-04-24T00:00:00+00:00",
            "last_used_at": "2026-04-24T00:00:00+00:00",
        },
    )

    async def fake_collect_history():  # noqa: ANN202
        return [{"message_id": 10, "role": "user", "content": "Зайка"}]

    async def fake_generate_reply(history):  # noqa: ANN202
        del history
        return "Нормальный ответ"

    runtime._collect_history = fake_collect_history  # type: ignore[method-assign]
    runtime._generate_auto_reply = fake_generate_reply  # type: ignore[method-assign]

    asyncio.run(runtime._reply_after_delay(trigger_message_id=10))

    session_state = store.get_json("codex_session")
    assert len(fake_client.sent_messages) == 1
    assert fake_client.action_calls == [(987654321, "typing")]
    assert session_state["thread_id"] == "thread-1"
    last_event = store.get_last_event()
    assert last_event is not None
    assert last_event["kind"] == "auto_reply_sent"


def test_send_voice_fallback_text_sends_text_and_updates_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime, store, fake_client = build_runtime(
        tmp_path,
        monkeypatch,
        latest_messages=[FakeMessage(10, out=False, raw_text="voice")],
    )
    asyncio.run(runtime.start())
    seed_message_state(store, last_incoming_message_id=10)

    delivered = asyncio.run(
        runtime._send_voice_fallback_text(
            trigger_message_id=10,
            reply_text="Текстовый fallback",
            reason="Voice reply failed, sent text fallback: boom",
        )
    )

    assert delivered is True
    assert [message.raw_text for message in fake_client.sent_messages] == ["Текстовый fallback"]
    message_state = store.get_json("message_state")
    assert message_state["last_auto_replied_incoming_message_id"] == 10
    assert fake_client.action_calls == [(987654321, "typing")]
    last_event = store.get_last_event()
    assert last_event is not None
    assert last_event["kind"] == "alert"
    assert last_event["payload"]["kind"] == "voice_fallback"
