# Project Plan

## Scope

Build a small self-hosted assistant for one consent-based Telegram dialog.

The first public version stays intentionally small:

- one configured target chat;
- one owner-only control bot;
- local `codex exec` backend;
- persistent Codex session state in SQLite;
- optional voice transcription and voice replies;
- clear handoff behavior when automation should stop.

## Out Of Scope

- multi-user dashboards;
- mass messaging;
- CRM integrations;
- public SaaS hosting;
- hidden automation or non-consensual impersonation;
- storing Telegram credentials in git.

## Architecture

```text
Telegram user account
        |
      Telethon
        |
PersonalAssistantRuntime
        |
SQLite state ---- Codex CLI session
        |
Telegram control bot
```

Voice path:

```text
Telegram voice note -> ffmpeg -> Groq Whisper -> Codex -> edge-tts -> Telegram voice note
```

## Roadmap

- Improve first-run setup for creating a Telethon `StringSession`.
- Add Docker and example `systemd` unit.
- Add optional per-chat profiles while keeping single-chat mode as the default.
- Add safer prompt templates for common use cases.
- Add structured runtime logs.
