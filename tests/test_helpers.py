from tg_personal_assistant.assistant import (
    build_reply_prompt,
    build_runtime_settings_state,
    build_session_bootstrap_prompt,
    build_session_turn_prompt,
    contains_phrase_trigger,
    estimate_typing_window_seconds,
    has_newer_outgoing_than_trigger,
    normalize_text,
    prompt_fingerprint,
)


def test_normalize_text_keeps_slash_and_collapses_spaces() -> None:
    assert normalize_text("  Это   ты   или бот?!  ") == "это ты или бот"
    assert normalize_text("/stop!!!") == "/stop"


def test_contains_phrase_trigger_works_on_substring() -> None:
    assert contains_phrase_trigger(
        "Слушай, это ты или бот вообще?",
        ["это ты или бот?"],
    )
    assert not contains_phrase_trigger("просто привет", ["позови человека"])


def test_build_reply_prompt_prioritizes_latest_message() -> None:
    prompt = build_reply_prompt("Базовый промпт.", "Зайка")
    assert "Последнее входящее сообщение: Зайка" in prompt
    assert "не уводи ответ в старую тему" in prompt


def test_build_session_bootstrap_prompt_includes_transcript() -> None:
    prompt = build_session_bootstrap_prompt(
        "Базовый промпт.",
        [
            {"role": "user", "content": "Привет"},
            {"role": "assistant", "content": "Привет, моя хорошая"},
        ],
    )
    assert "CONTACT: Привет" in prompt
    assert "ТЫ: Привет, моя хорошая" in prompt


def test_build_session_turn_prompt_anchors_latest_incoming() -> None:
    prompt = build_session_turn_prompt(
        [
            {"role": "assistant", "content": "Я тут"},
            {"role": "user", "content": "Зайка"},
        ]
    )
    assert "Новые сообщения:" in prompt
    assert "Последнее входящее сообщение от собеседника: Зайка" in prompt


def test_build_runtime_settings_state_preserves_persisted_prompt() -> None:
    state = build_runtime_settings_state(
        {"system_prompt": "Сохраненный промпт"},
        configured_prompt="Новый промпт из конфига",
        phrase_triggers=["позови человека"],
        model="gpt-5.4",
    )
    assert state["system_prompt"] == "Сохраненный промпт"
    assert state["prompt_fingerprint"] == prompt_fingerprint("Сохраненный промпт")


def test_has_newer_outgoing_than_trigger_detects_manual_reply() -> None:
    assert has_newer_outgoing_than_trigger(
        10,
        11,
        latest_message_is_outgoing=True,
    )
    assert not has_newer_outgoing_than_trigger(
        10,
        11,
        latest_message_is_outgoing=False,
    )


def test_estimate_typing_window_seconds_stays_short() -> None:
    assert estimate_typing_window_seconds("ок") == 1.0
    assert estimate_typing_window_seconds("Давай увидимся вечером после дел, если ты будешь свободна") == 1.6
    assert estimate_typing_window_seconds("Очень длинное сообщение " * 12) == 2.2
