from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path


APP_BUNDLED_CODEX = Path("/Applications/Codex.app/Contents/Resources/codex")


@dataclass(slots=True)
class CodexReply:
    text: str
    thread_id: str | None


class CodexCliClient:
    def __init__(
        self,
        model: str,
        *,
        codex_command: str = "codex",
        timeout_sec: int = 180,
        workspace_dir: str | Path | None = None,
    ) -> None:
        self._model = model
        self._timeout_sec = timeout_sec
        self._codex_command = self._resolve_codex_command(codex_command)
        if workspace_dir is None:
            workspace_dir = Path(tempfile.gettempdir()) / "tg-personal-assistant-codex"
        self._workspace_dir = Path(workspace_dir).expanduser().resolve()
        self._workspace_dir.mkdir(parents=True, exist_ok=True)

    async def close(self) -> None:
        return None

    async def generate_reply(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.8,
        max_tokens: int = 300,
    ) -> str:
        del temperature, max_tokens
        prompt = self._build_prompt(system_prompt, messages)
        result = await self._run_codex(
            subcommand=[],
            prompt=prompt,
        )
        return result.text

    async def start_session(self, prompt: str) -> CodexReply:
        result = await self._run_codex(
            subcommand=[],
            prompt=prompt,
        )
        if not result.thread_id:
            raise RuntimeError("Codex CLI did not return a thread_id for a new session")
        return result

    async def resume_session(self, thread_id: str, prompt: str) -> CodexReply:
        return await self._run_codex(
            subcommand=["resume", "--model", self._model, thread_id],
            prompt=prompt,
        )

    def _resolve_codex_command(self, codex_command: str) -> str:
        candidate = Path(codex_command).expanduser()
        if candidate.exists():
            return str(candidate)
        resolved = shutil.which(codex_command)
        if resolved:
            return resolved
        if APP_BUNDLED_CODEX.exists():
            return str(APP_BUNDLED_CODEX)
        raise FileNotFoundError(
            f"Could not find Codex CLI binary: {codex_command}"
        )

    def _build_prompt(self, system_prompt: str, messages: list[dict[str, str]]) -> str:
        transcript_lines = []
        for item in messages:
            role = str(item.get("role", "user")).strip().lower() or "user"
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            transcript_lines.append(f"{role.upper()}:\n{content}")
        transcript = "\n\n".join(transcript_lines) if transcript_lines else "Нет истории."
        return (
            "Ты пишешь одно итоговое сообщение для Telegram.\n"
            "Верни только готовый текст ответа, без анализа, вступлений и служебных комментариев.\n\n"
            "Системные инструкции:\n"
            f"{system_prompt.strip()}\n\n"
            "Диалог:\n"
            f"{transcript}\n"
        )

    async def _run_codex(
        self,
        *,
        subcommand: list[str],
        prompt: str,
    ) -> CodexReply:
        with tempfile.TemporaryDirectory(prefix="tg-personal-codex-output-") as temp_dir:
            output_path = Path(temp_dir) / "last-message.txt"
            env = os.environ.copy()
            env.setdefault("TERM", "dumb")
            args = [
                self._codex_command,
                "exec",
                "--json",
                "--skip-git-repo-check",
                "--ignore-user-config",
                "--ignore-rules",
                "--output-last-message",
                str(output_path),
                "--cd",
                str(self._workspace_dir),
            ]
            if not subcommand:
                args.extend(
                    [
                        "--model",
                        self._model,
                        "--sandbox",
                        "read-only",
                        "-",
                    ]
                )
            else:
                args.extend(subcommand)
                args.append("-")

            process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(prompt.encode("utf-8")),
                    timeout=self._timeout_sec,
                )
            except asyncio.TimeoutError as exc:
                process.kill()
                await process.communicate()
                raise RuntimeError(
                    f"Codex CLI timed out after {self._timeout_sec}s"
                ) from exc

            stdout_text = stdout.decode("utf-8", errors="ignore")
            stderr_text = stderr.decode("utf-8", errors="ignore")
            if process.returncode != 0:
                diagnostics = "\n".join(
                    part.strip()
                    for part in (
                        stdout_text[-1200:],
                        stderr_text[-1200:],
                    )
                    if part.strip()
                )
                raise RuntimeError(
                    "Codex CLI failed"
                    + (f": {diagnostics}" if diagnostics else "")
                )

            thread_id = None
            for line in stdout_text.splitlines():
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if payload.get("type") == "thread.started":
                    thread_id = payload.get("thread_id")

            text = output_path.read_text(encoding="utf-8").strip() if output_path.exists() else ""
            if not text:
                diagnostics = stdout_text[-1200:].strip()
                raise ValueError(
                    "Codex CLI returned an empty reply"
                    + (f": {diagnostics}" if diagnostics else "")
                )
            return CodexReply(text=text, thread_id=thread_id)
