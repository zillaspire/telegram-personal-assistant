from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path

import httpx


GROQ_TRANSCRIPT_ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"


def format_incoming_voice_for_context(transcript: str) -> str:
    return f"(голосовое сообщение) {transcript.strip()}"


def format_outgoing_voice_for_context(reply_text: str) -> str:
    return f"(твой голосовой ответ) {reply_text.strip()}"


def is_voice_message(message: object) -> bool:
    if bool(getattr(message, "voice", False)):
        return True
    file_info = getattr(message, "file", None)
    mime_type = str(getattr(file_info, "mime_type", "") or "").casefold()
    return mime_type in {"audio/ogg", "audio/opus", "audio/mp4"}


class GroqVoiceTranscriber:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = "whisper-large-v3-turbo",
        timeout_sec: int = 120,
    ) -> None:
        self._api_key = api_key.strip()
        self._model = model.strip()
        self._timeout_sec = timeout_sec

    async def transcribe(self, audio_path: str | Path) -> str:
        source_path = Path(audio_path).expanduser().resolve()
        content_type, _ = mimetypes.guess_type(source_path.name)
        headers = {"Authorization": f"Bearer {self._api_key}"}
        data = {"model": self._model, "language": "ru"}
        files = {
            "file": (
                source_path.name,
                source_path.read_bytes(),
                content_type or "application/octet-stream",
            )
        }
        async with httpx.AsyncClient(timeout=self._timeout_sec) as client:
            response = await client.post(
                GROQ_TRANSCRIPT_ENDPOINT,
                headers=headers,
                data=data,
                files=files,
            )
        response.raise_for_status()
        payload = response.json()
        text = str(payload.get("text", "")).strip()
        if not text:
            raise ValueError("Groq transcription returned an empty text")
        return text


async def convert_audio_for_transcription(
    source_path: str | Path,
    *,
    ffmpeg_command: str = "ffmpeg",
) -> Path:
    input_path = Path(source_path).expanduser().resolve()
    output_path = input_path.with_suffix(".wav")
    process = await asyncio.create_subprocess_exec(
        ffmpeg_command,
        "-y",
        "-i",
        str(input_path),
        "-ar",
        "16000",
        "-ac",
        "1",
        str(output_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        diagnostics = "\n".join(
            part.strip()
            for part in (
                stdout.decode("utf-8", errors="ignore")[-1200:],
                stderr.decode("utf-8", errors="ignore")[-1200:],
            )
            if part.strip()
        )
        raise RuntimeError(
            "ffmpeg failed to convert incoming audio for transcription"
            + (f": {diagnostics}" if diagnostics else "")
        )
    return output_path


class EdgeVoiceNoteSynthesizer:
    def __init__(
        self,
        *,
        voice: str = "ru-RU-DmitryNeural",
        ffmpeg_command: str = "ffmpeg",
    ) -> None:
        self._voice = voice.strip()
        self._ffmpeg_command = ffmpeg_command

    async def synthesize_voice_note(
        self,
        text: str,
        *,
        output_dir: str | Path,
    ) -> Path:
        temp_dir = Path(output_dir).expanduser().resolve()
        temp_dir.mkdir(parents=True, exist_ok=True)
        mp3_path = temp_dir / "voice-reply.mp3"
        ogg_path = temp_dir / "voice-reply.ogg"

        import edge_tts

        communicate = edge_tts.Communicate(
            text=text.strip(),
            voice=self._voice,
        )
        await communicate.save(str(mp3_path))
        await self._convert_to_telegram_voice(mp3_path, ogg_path)
        return ogg_path

    async def _convert_to_telegram_voice(self, source_path: Path, target_path: Path) -> None:
        process = await asyncio.create_subprocess_exec(
            self._ffmpeg_command,
            "-y",
            "-i",
            str(source_path),
            "-vn",
            "-c:a",
            "libopus",
            "-b:a",
            "48k",
            "-vbr",
            "on",
            "-compression_level",
            "10",
            str(target_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            diagnostics = "\n".join(
                part.strip()
                for part in (
                    stdout.decode("utf-8", errors="ignore")[-1200:],
                    stderr.decode("utf-8", errors="ignore")[-1200:],
                )
                if part.strip()
            )
            raise RuntimeError(
                "ffmpeg failed to convert synthesized voice note"
                + (f": {diagnostics}" if diagnostics else "")
            )
