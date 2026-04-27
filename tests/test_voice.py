import asyncio
from types import SimpleNamespace

from pathlib import Path

from tg_personal_assistant.voice import (
    convert_audio_for_transcription,
    format_incoming_voice_for_context,
    format_outgoing_voice_for_context,
    is_voice_message,
)


def test_voice_context_formatters_add_modality_markers() -> None:
    assert format_incoming_voice_for_context("привет") == "(голосовое сообщение) привет"
    assert format_outgoing_voice_for_context("я скоро буду") == "(твой голосовой ответ) я скоро буду"


def test_is_voice_message_detects_voice_flag_and_audio_mime() -> None:
    assert is_voice_message(SimpleNamespace(voice=True, file=None))
    assert is_voice_message(
        SimpleNamespace(
            voice=False,
            file=SimpleNamespace(mime_type="audio/ogg"),
        )
    )
    assert not is_voice_message(
        SimpleNamespace(
            voice=False,
            file=SimpleNamespace(mime_type="image/jpeg"),
        )
    )


def test_convert_audio_for_transcription_uses_wav_suffix(tmp_path, monkeypatch) -> None:
    source = tmp_path / "voice.oga"
    source.write_bytes(b"fake")

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            output = source.with_suffix(".wav")
            output.write_bytes(b"wav")
            return b"", b""

    async def fake_create_subprocess_exec(*args, **kwargs):  # noqa: ANN202
        del args, kwargs
        return FakeProcess()

    monkeypatch.setattr("tg_personal_assistant.voice.asyncio.create_subprocess_exec", fake_create_subprocess_exec)
    result = asyncio.run(convert_audio_for_transcription(source))
    assert result == Path(tmp_path / "voice.wav")
    assert result.exists()
