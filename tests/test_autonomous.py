import asyncio
import time
from types import SimpleNamespace

import pytest

from tg_personal_assistant.assistant import PersonalAssistantRuntime, prompt_fingerprint
from tg_personal_assistant.config import AutonomousSettings
from tg_personal_assistant.state import StateStore
from test_runtime import FakeMessage, build_runtime, seed_message_state


class Model:
    def __init__(self):
        self.calls = []
        self.generated = asyncio.Event()
        self.release = None

    async def generate_reply(self, prompt, messages):
        self.calls.append((prompt, messages))
        self.generated.set()
        if self.release:
            await self.release.wait()
        return "Claude reply"

    async def close(self):
        pass


async def setup(tmp_path, monkeypatch, **settings):
    runtime, store, client = build_runtime(tmp_path, monkeypatch)
    runtime._settings.autonomous = AutonomousSettings(
        **({"min_interval_sec": 0, "max_interval_sec": 0} | settings)
    )
    model = Model()
    runtime._llm_client = model
    await runtime.start()
    await runtime.restore_autonomous()
    return runtime, store, client, model


async def incoming(runtime, client, text, *, duplicate=None):
    message = duplicate or FakeMessage(
        max((item.id for item in client.latest_messages), default=0) + 1, out=False, raw_text=text,
    )
    if duplicate is None:
        client.latest_messages.insert(0, message)
    message.peer_id = SimpleNamespace(user_id=987654321)
    await runtime._on_new_message(SimpleNamespace(message=message))
    return message


async def worker(runtime):
    await asyncio.wait_for(runtime._pending_reply_task, 2)


def test_start_repeat_first_and_no_unsolicited_followup(tmp_path, monkeypatch):
    async def check():
        runtime, store, client, model = await setup(tmp_path, monkeypatch)
        await runtime.start_autonomous("Ask about the trip")
        session = store.get_json("autonomous")
        assert session["status"] == "active"
        await runtime.start_autonomous("Duplicate")
        assert session["id"] == store.get_json("autonomous")["id"]
        await worker(runtime)
        assert len(client.sent_messages) == 1
        assert "ИИ-ассистент" in client.sent_messages[0].raw_text
        assert "/stop" in client.sent_messages[0].raw_text
        assert "Ask about the trip" in model.calls[0][0]
        await runtime.restore_autonomous()
        await worker(runtime)
        assert len(client.sent_messages) == 1
    asyncio.run(check())


def test_response_context_duplicate_and_reactive_exclusion(tmp_path, monkeypatch):
    async def check():
        runtime, store, client, model = await setup(tmp_path, monkeypatch, history_limit=3)
        await runtime.start_autonomous("")
        await worker(runtime)
        await runtime.set_mode("auto")
        assert not runtime._should_auto_reply()
        msg = await incoming(runtime, client, "The trip was great")
        await worker(runtime)
        assert len(client.sent_messages) == 2
        assert "The trip was great" in model.calls[-1][1][0]["content"]
        assert "ИИ-ассистент" in model.calls[-1][1][0]["content"]
        await incoming(runtime, client, msg.raw_text, duplicate=msg)
        await worker(runtime)
        assert len(client.sent_messages) == 2
        assert len(model.calls) == 2
    asyncio.run(check())


@pytest.mark.parametrize("phase", ["claude", "delay"])
def test_owner_stop_cancels_work(tmp_path, monkeypatch, phase):
    async def check():
        interval = 30 if phase == "delay" else 0
        runtime, store, client, model = await setup(tmp_path, monkeypatch, min_interval_sec=interval, max_interval_sec=interval)
        if phase == "claude":
            model.release = asyncio.Event()
        await runtime.start_autonomous("")
        await asyncio.wait_for(model.generated.wait(), 1)
        task = runtime._pending_reply_task
        await runtime.stop_autonomous()
        with pytest.raises(asyncio.CancelledError):
            await task
        await runtime.resume()
        assert store.get_json("autonomous")["status"] == "stopped"
        assert client.sent_messages == []
    asyncio.run(check())


def test_pause_blocks_and_resume_releases_autonomous_send(tmp_path, monkeypatch):
    async def check():
        runtime, _, client, model = await setup(tmp_path, monkeypatch)
        model.release = asyncio.Event()
        await runtime.start_autonomous("")
        await model.generated.wait()
        await runtime.pause_for(30)
        model.release.set()
        await asyncio.sleep(0)
        assert not client.sent_messages
        await runtime.resume()
        await worker(runtime)
        assert len(client.sent_messages) == 1
    asyncio.run(check())


def test_auto_off_and_contact_stop(tmp_path, monkeypatch):
    async def check():
        runtime, store, client, _ = await setup(tmp_path, monkeypatch)
        await runtime.start_autonomous("")
        await worker(runtime)
        await incoming(runtime, client, "/stop")
        assert store.get_json("autonomous")["reason"] == "slash_stop"
        await runtime.resume()
        assert not runtime._autonomous_session()
        await runtime.start_autonomous("")
        await runtime.set_mode("off")
        await asyncio.sleep(0)
        assert store.get_json("autonomous")["reason"] == "auto_off"
        assert not runtime._should_auto_reply()
        assert len(client.sent_messages) == 1
    asyncio.run(check())


def test_owner_reply_during_generation(tmp_path, monkeypatch):
    async def check():
        runtime, store, client, model = await setup(tmp_path, monkeypatch)
        await runtime.start_autonomous("")
        await worker(runtime)
        model.generated.clear()
        model.release = asyncio.Event()
        message = await incoming(runtime, client, "Tell me more")
        await model.generated.wait()
        client.latest_messages.insert(0, FakeMessage(message.id + 1, out=True, raw_text="I took over"))
        model.release.set()
        await worker(runtime)
        assert len(client.sent_messages) == 1
        assert store.get_json("autonomous")["reason"] == "manual_takeover"
    asyncio.run(check())


@pytest.mark.parametrize("accepted", [True, False])
def test_restart_reconciles_uncertain_first_send(tmp_path, monkeypatch, accepted):
    async def check():
        runtime, store, client, _ = await setup(tmp_path, monkeypatch)
        await runtime.start_autonomous("")
        session = store.get_json("autonomous")
        runtime._cancel_pending()
        text = "Committed at Telegram"
        assert store.reserve_outgoing(987654321, f"autonomous:{session['id']}:first", 0, "text:" + prompt_fingerprint(text))
        if accepted:
            client.latest_messages.insert(0, FakeMessage(1, out=True, raw_text=text))
        restarted = PersonalAssistantRuntime(runtime._settings, StateStore(runtime._settings.sqlite_db_path), Model())
        await restarted.start(defer_outgoing=True)
        await restarted.restore_autonomous()
        if accepted:
            await worker(restarted)
            assert restarted._autonomous_session()["sent"] == 1
            await incoming(restarted, client, "Continue")
            await worker(restarted)
            assert len(client.sent_messages) == 1
        else:
            assert store.get_json("autonomous")["reason"] == "delivery_unknown"
            assert len(client.sent_messages) == 0
    asyncio.run(check())


def test_message_and_duration_limits(tmp_path, monkeypatch):
    async def check():
        runtime, store, client, _ = await setup(tmp_path, monkeypatch, max_messages=1)
        await runtime.start_autonomous("")
        await worker(runtime)
        assert store.get_json("autonomous")["reason"] == "limit"
        await incoming(runtime, client, "No second reply")
        assert len(client.sent_messages) == 1
        await runtime.start_autonomous("")
        session = store.get_json("autonomous")
        session["started_at"] = time.time() - 4000
        store.set_json("autonomous", session)
        await worker(runtime)
        assert len(client.sent_messages) == 1
    asyncio.run(check())


def test_voice_synthesis_cancelled_before_send(tmp_path, monkeypatch):
    async def check():
        runtime, store, client, _ = await setup(tmp_path, monkeypatch)
        entered = asyncio.Event()
        async def synthesize(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        async def transcript(*args, **kwargs):
            return "transcript"
        async def path(*args, **kwargs):
            return tmp_path / "mock.ogg"
        async def generate(*args):
            return "voice reply"
        runtime._voice_transcriber = SimpleNamespace(transcribe=transcript)
        runtime._voice_synthesizer = SimpleNamespace(synthesize_voice_note=synthesize)
        runtime._generate_auto_reply = generate
        client.download_media = path
        monkeypatch.setattr("tg_personal_assistant.assistant.convert_audio_for_transcription", path)
        voice = FakeMessage(10, out=False, raw_text="")
        client.latest_messages.insert(0, voice)
        seed_message_state(store, last_incoming_message_id=10)
        task = asyncio.create_task(runtime._reply_with_voice_after_delay(trigger_message_id=10, source_message=voice))
        runtime._pending_reply_task = task
        await asyncio.wait_for(entered.wait(), 1)
        await runtime.set_mode("off")
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not client.sent_messages
        assert store.pending_outgoing(987654321) is None
    asyncio.run(check())


def test_common_sender_deduplicates_concurrent_replies(tmp_path, monkeypatch):
    async def check():
        runtime, store, client, _ = await setup(tmp_path, monkeypatch)
        client.latest_messages = [FakeMessage(10, out=False, raw_text="incoming")]
        seed_message_state(store, last_incoming_message_id=10)
        sent = await asyncio.gather(
            runtime._send_guarded(10, text="one"), runtime._send_guarded(10, text="two"),
        )
        assert sum(item is not None for item in sent) == 1
        assert len(client.sent_messages) == 1
    asyncio.run(check())
