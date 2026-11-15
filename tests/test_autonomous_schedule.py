import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

import tg_personal_assistant.assistant as assistant_module
from tg_personal_assistant.assistant import PersonalAssistantRuntime
from tg_personal_assistant.autonomous_schedule import DEFAULT_SCHEDULE, current_window
from tg_personal_assistant.control_bot import AUTONOMOUS_SETTINGS_COMMANDS, ControlBotManager
from test_autonomous import Model, incoming, setup, worker


def kyiv(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("Europe/Kyiv")).timestamp()


@pytest.fixture
def clock(monkeypatch):
    clock = SimpleNamespace(now=kyiv(2026, 11, 16, 17, 0))
    monkeypatch.setattr(assistant_module, "time", SimpleNamespace(time=lambda: clock.now))
    return clock


@pytest.mark.parametrize("minutes", [15, 300])
def test_manual_duration_and_expiry(tmp_path, monkeypatch, clock, minutes):
    async def check():
        runtime, store, client, _ = await setup(tmp_path, monkeypatch)
        runtime.attach_control_bot(SimpleNamespace(send_owner_message=AsyncMock()))
        await runtime.configure_autonomous("duration", [str(minutes)])
        await runtime.start_autonomous("")
        await worker(runtime)
        session = store.get_json("autonomous")
        assert session["ends_at"] - session["started_at"] == minutes * 60
        clock.now = session["ends_at"]
        await runtime._scheduler_tick()
        assert store.get_json("autonomous")["status"] == "inactive"
        assert store.get_json("autonomous")["reason"] == "expired"
        runtime._control_bot.send_owner_message.assert_awaited_once()
        await incoming(runtime, client, "hi")
        assert len(client.sent_messages) == 1
        await runtime.stop()
    asyncio.run(check())


@pytest.mark.parametrize("value", ["14", "301", "abc"])
def test_rejects_invalid_duration(tmp_path, monkeypatch, value):
    async def check():
        runtime, _, _, _ = await setup(tmp_path, monkeypatch)
        with pytest.raises(ValueError):
            await runtime.configure_autonomous("duration", [value])
        await runtime.stop()
    asyncio.run(check())


@pytest.mark.parametrize("manual", [True, False])
def test_scheduled_start_end_and_recovery(tmp_path, monkeypatch, clock, manual):
    async def check():
        runtime, store, client, _ = await setup(tmp_path, monkeypatch)
        await runtime.configure_autonomous("duration", ["300"])
        await runtime.configure_autonomous("set_days", ["mon"])
        await runtime.configure_autonomous("schedule_on", [])
        await runtime.start_autonomous("outside")
        assert not runtime._autonomous_session()
        clock.now = kyiv(2026, 11, 16, 22, 0)
        if manual:
            await runtime.start_autonomous("manual")
        else:
            await runtime._scheduler_tick()
        await worker(runtime)
        previous = runtime._autonomous_session()
        assert previous["mode"] == ("manual" if manual else "scheduled")
        assert previous["ends_at"] == kyiv(2026, 11, 16, 23, 0)
        await runtime.stop()
        restarted = PersonalAssistantRuntime(runtime._settings, store, Model())
        await restarted.start(defer_outgoing=True)
        await restarted.restore_autonomous()
        await worker(restarted)
        assert restarted._autonomous_session()["id"] == previous["id"]
        assert len(client.sent_messages) == 1
        clock.now = previous["ends_at"]
        await restarted._scheduler_tick()
        assert store.get_json("autonomous")["reason"] == "schedule_end"
        await restarted.stop()
    asyncio.run(check())


def test_one_opener_per_window_and_auto_off_disarms(tmp_path, monkeypatch, clock):
    async def check():
        runtime, _, client, _ = await setup(tmp_path, monkeypatch)
        await runtime.configure_autonomous("duration", ["15"])
        clock.now = kyiv(2026, 11, 16, 18, 0)
        await runtime.configure_autonomous("schedule_on", [])
        await worker(runtime)
        clock.now += 16 * 60
        await runtime._scheduler_tick()
        await runtime._scheduler_tick()
        assert not runtime._autonomous_session()
        assert len(client.sent_messages) == 1
        await runtime.set_mode("off")
        clock.now = kyiv(2026, 11, 17, 18, 0)
        await runtime._scheduler_tick()
        assert not runtime._autonomous_session()
        await runtime.stop()
    asyncio.run(check())


def test_unlimited_and_optional_limit(tmp_path, monkeypatch, clock):
    async def check():
        runtime, store, client, _ = await setup(tmp_path, monkeypatch)
        assert runtime._settings.autonomous.max_messages == 0
        await runtime.start_autonomous("")
        await worker(runtime)
        session = runtime._autonomous_session()
        session["sent"] = 37
        store.set_json("autonomous", session)
        await incoming(runtime, client, "still here")
        await worker(runtime)
        assert runtime._autonomous_session()["sent"] == 38
        await runtime.configure_autonomous("limit", ["38"])
        assert store.get_json("autonomous")["reason"] == "limit"
        await runtime.configure_autonomous("unlimited", [])
        assert "без фиксированного лимита" in runtime.describe_autonomous()
        await runtime.stop()
    asyncio.run(check())


def test_overnight_uses_start_day_and_kyiv():
    schedule = DEFAULT_SCHEDULE | {"enabled": True, "days": ["mon"], "start": "22:00", "end": "03:00"}
    assert current_window(schedule, kyiv(2026, 11, 17, 2)) == (
        kyiv(2026, 11, 16, 22), kyiv(2026, 11, 17, 3),
    )
    assert not current_window(schedule, kyiv(2026, 11, 17, 3))
    assert not current_window(schedule, kyiv(2026, 11, 18, 2))


def test_dst_fold_uses_earliest_opening_and_latest_closing():
    zone = ZoneInfo("Europe/Kyiv")
    schedule = DEFAULT_SCHEDULE | {"enabled": True, "start": "03:10", "end": "03:50"}
    early = datetime(2026, 10, 25, 3, 10, tzinfo=zone, fold=0).timestamp()
    late = datetime(2026, 10, 25, 3, 50, tzinfo=zone, fold=1).timestamp()
    assert late - early == 100 * 60
    assert current_window(schedule, early + 70 * 60) == (early, late)


@pytest.mark.parametrize("action", AUTONOMOUS_SETTINGS_COMMANDS.keys())
def test_new_setting_commands_are_owner_only(action):
    async def check():
        assistant = SimpleNamespace(
            configure_autonomous=AsyncMock(return_value="ok"),
            describe_autonomous_schedule=lambda: "schedule",
        )
        manager = ControlBotManager("mock-token", 1, assistant)
        handler = manager._owner_only(manager._autonomous_settings_handler(action))
        reply = AsyncMock()
        update = SimpleNamespace(effective_user=SimpleNamespace(id=2),
                                 effective_message=SimpleNamespace(reply_text=reply))
        await handler(update, SimpleNamespace(args=[]))
        reply.assert_not_awaited()
        assistant.configure_autonomous.assert_not_awaited()
        update.effective_user.id = 1
        await handler(update, SimpleNamespace(args=[]))
        reply.assert_awaited_once()
    asyncio.run(check())
