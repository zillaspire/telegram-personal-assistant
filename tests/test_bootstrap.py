from pathlib import Path

from tg_personal_assistant.bootstrap import _extract_env_or_commented_value


def test_extracts_commented_telethon_values(tmp_path: Path) -> None:
    sample = tmp_path / ".env"
    sample.write_text(
        "# TG_USERBOT_SESSION=session-value\n"
        "# TG_API_ID=123\n"
        "# TG_API_HASH=hash-value\n",
        encoding="utf-8",
    )
    assert _extract_env_or_commented_value(sample, "TG_USERBOT_SESSION") == "session-value"
    assert _extract_env_or_commented_value(sample, "TG_API_ID") == "123"
    assert _extract_env_or_commented_value(sample, "TG_API_HASH") == "hash-value"
