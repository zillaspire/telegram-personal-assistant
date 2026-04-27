# Telegram Personal Assistant

A small self-hosted Telegram assistant for one private chat.

It reads incoming messages from your own Telegram account, keeps a persistent Codex session for the selected dialog, and can reply automatically when auto mode is enabled. A separate Telegram control bot lets you pause the assistant, change the prompt, draft a first message, reset the model session, and inspect the last event.

This project is deliberately narrow. It is not a mass-messaging bot, not a growth-hacking tool, and not a way to impersonate someone without consent.

## What It Does

- Watches one allowlisted Telegram dialog through Telethon.
- Replies through your Telegram user account, after a short delay and typing indicator.
- Uses `codex exec` with local Codex auth instead of storing a model API key.
- Keeps one persistent Codex session per target chat in SQLite.
- Lets the owner control the service through a separate Telegram Bot API bot.
- Stops auto-replying on configurable handoff phrases like “I want to talk to you directly”.
- Can transcribe incoming voice messages with Groq Whisper and answer with a generated Telegram voice note.

## Safety Model

Use this only in chats where everyone involved understands what is happening and has agreed to the experiment.

The service has a few guardrails by default:

- Owner-only control bot: commands are ignored unless they come from your Telegram user ID.
- Single target chat: the assistant only watches the configured `target_chat_id`.
- Handoff triggers: sensitive phrases pause automation and alert the owner instead of replying.
- Manual override: if you reply yourself before the pending auto-reply is sent, the pending reply is cancelled.
- Local secrets: Telegram sessions, bot tokens, and API keys live in `secrets.env`, which is ignored by git.

## Requirements

- Python 3.11+
- A Telegram API ID and hash from [my.telegram.org](https://my.telegram.org)
- A Telethon `StringSession` for the Telegram account that will send replies
- A Telegram control bot token from [BotFather](https://t.me/BotFather)
- Local Codex CLI auth (`codex exec` must work on the machine)
- Optional: `GROQ_API_KEY` and `ffmpeg` for voice transcription/replies

## Quick Start

```bash
git clone https://github.com/your-name/telegram-personal-assistant.git
cd telegram-personal-assistant

python3.11 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

cp config.toml.example config.toml
cp .env.example secrets.env
```

Fill in `secrets.env`:

```dotenv
TG_API_ID=123456
TG_API_HASH=your_telegram_api_hash
TG_USER_SESSION=your_telethon_string_session
CONTROL_BOT_TOKEN=123456:bot_token_from_botfather
GROQ_API_KEY=
```

Find the chat ID:

```bash
tg-personal-list-dialogs --secrets secrets.env --limit 50
```

Put that ID into `config.toml` as `target_chat_id`, set your own Telegram ID as `owner_telegram_id`, then run:

```bash
tg-personal-assistant --config config.toml --secrets secrets.env
```

Open your control bot in Telegram and send `/start`.

## Control Commands

- `/status` shows mode, pause state, target chat, and session state.
- `/auto_on` enables automatic replies.
- `/auto_off` disables automatic replies.
- `/pause_30m` and `/pause_2h` pause replies temporarily.
- `/resume` removes the pause.
- `/show_prompt` shows the current system prompt.
- `/edit_prompt` makes your next message the new system prompt.
- `/show_session` shows the current Codex session state.
- `/reset_session` starts a fresh Codex session on the next incoming message.
- `/draft_first_message` generates a draft without sending it to the target chat.
- `/cancel_input` cancels prompt editing or draft mode.
- `/last_event` shows the last alert, error, or notable runtime event.

## Voice Messages

Voice support is optional.

If `GROQ_API_KEY` is present and `voice_replies_enabled = true`, incoming Telegram voice notes are converted with `ffmpeg`, transcribed through Groq Whisper, passed into the same dialog session, and answered as a Telegram voice note using `edge-tts`.

If voice generation fails after text is already generated, the assistant sends the text as a fallback and alerts the owner.

## Configuration

`config.toml.example` contains the full shape:

- `target_chat_id`: the only chat the assistant watches.
- `owner_telegram_id`: the only user allowed to control the bot.
- `phrase_triggers`: phrases that force a handoff to the owner.
- `system_prompt`: your assistant style and boundaries.
- `auto_reply_min_delay_sec` / `auto_reply_max_delay_sec`: small natural delay before replying.
- `voice_replies_enabled`: enables or disables the voice pipeline.

Keep `config.toml`, `secrets.env`, and `data/` out of git.

## Deployment Notes

The project is intentionally simple to deploy:

1. Copy the repo to your server.
2. Install Python dependencies in a virtual environment.
3. Install `codex` and authenticate it locally.
4. Add `config.toml` and `secrets.env`.
5. Run it under `systemd`, `supervisord`, Docker, or your process manager of choice.

See [docs/RUNBOOK.md](docs/RUNBOOK.md) for a plain server checklist.

## Development

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

## License

MIT. See [LICENSE](LICENSE).
