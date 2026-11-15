from tg_personal_assistant.control_bot import (
    BUTTON_AUTO_OFF,
    BUTTON_AUTO_ON,
    BUTTON_CANCEL_INPUT,
    BUTTON_DRAFT_FIRST_MESSAGE,
    BUTTON_EDIT_PROMPT,
    BUTTON_HELP,
    BUTTON_LAST_EVENT,
    BUTTON_PAUSE_2H,
    BUTTON_PAUSE_30M,
    BUTTON_RESET_SESSION,
    BUTTON_RESUME,
    BUTTON_SHOW_SESSION,
    BUTTON_SHOW_PROMPT,
    BUTTON_STATUS,
    BUTTON_TO_ACTION,
    build_control_keyboard,
)


def test_button_mapping_covers_main_actions() -> None:
    assert BUTTON_TO_ACTION[BUTTON_STATUS] == "status"
    assert BUTTON_TO_ACTION[BUTTON_AUTO_ON] == "auto_on"
    assert BUTTON_TO_ACTION[BUTTON_AUTO_OFF] == "auto_off"
    assert BUTTON_TO_ACTION[BUTTON_PAUSE_30M] == "pause_30m"
    assert BUTTON_TO_ACTION[BUTTON_PAUSE_2H] == "pause_2h"
    assert BUTTON_TO_ACTION[BUTTON_RESUME] == "resume"
    assert BUTTON_TO_ACTION[BUTTON_SHOW_PROMPT] == "show_prompt"
    assert BUTTON_TO_ACTION[BUTTON_SHOW_SESSION] == "show_session"
    assert BUTTON_TO_ACTION[BUTTON_RESET_SESSION] == "reset_session"
    assert BUTTON_TO_ACTION[BUTTON_DRAFT_FIRST_MESSAGE] == "draft_first_message"
    assert BUTTON_TO_ACTION[BUTTON_LAST_EVENT] == "last_event"
    assert BUTTON_TO_ACTION[BUTTON_HELP] == "help"


def test_keyboard_contains_readable_buttons() -> None:
    keyboard = build_control_keyboard()
    labels = [button.text for row in keyboard.keyboard for button in row]
    assert BUTTON_STATUS in labels
    assert BUTTON_AUTO_ON in labels
    assert BUTTON_AUTO_OFF in labels
    assert BUTTON_PAUSE_30M in labels
    assert BUTTON_PAUSE_2H in labels
    assert BUTTON_RESUME in labels
    assert BUTTON_SHOW_PROMPT in labels
    assert BUTTON_EDIT_PROMPT in labels
    assert BUTTON_SHOW_SESSION in labels
    assert BUTTON_RESET_SESSION in labels
    assert BUTTON_DRAFT_FIRST_MESSAGE in labels
    assert BUTTON_CANCEL_INPUT in labels
    assert BUTTON_LAST_EVENT in labels
    assert BUTTON_HELP in labels


def test_button_mapping_includes_edit_and_cancel_actions() -> None:
    assert BUTTON_TO_ACTION[BUTTON_EDIT_PROMPT] == "edit_prompt"
    assert BUTTON_TO_ACTION[BUTTON_CANCEL_INPUT] == "cancel_input"



def test_offline_stop_is_applied_before_autonomy_restores():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from tg_personal_assistant.control_bot import ControlBotManager

    foreign = SimpleNamespace(
        effective_user=SimpleNamespace(id=2),
        effective_message=SimpleNamespace(text="/auto_off"),
        update_id=1,
    )
    owner = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_message=SimpleNamespace(text="/autonomous_stop"),
        update_id=2,
    )
    assistant = SimpleNamespace(stop_autonomous=AsyncMock(), set_mode=AsyncMock())
    bot = SimpleNamespace(get_updates=AsyncMock(side_effect=[[foreign, owner], []]))
    manager = ControlBotManager("mock-token", 1, assistant)
    manager._application = SimpleNamespace(bot=bot)
    asyncio.run(manager._process_offline_stops())
    assistant.stop_autonomous.assert_awaited_once_with()
    assistant.set_mode.assert_not_awaited()
    assert bot.get_updates.await_args.kwargs["offset"] == 3
