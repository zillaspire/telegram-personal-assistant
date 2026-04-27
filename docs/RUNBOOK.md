# Runbook

This is the short operational checklist for running Telegram Personal Assistant on a server.

## 1. Prepare The Project

```bash
git clone https://github.com/your-name/telegram-personal-assistant.git
cd telegram-personal-assistant

python3.11 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

## 2. Create Local Config Files

```bash
cp config.toml.example config.toml
cp .env.example secrets.env
```

Fill `secrets.env`:

- `TG_API_ID`
- `TG_API_HASH`
- `TG_USER_SESSION`
- `CONTROL_BOT_TOKEN`
- `GROQ_API_KEY` if you want voice transcription

You can also generate `secrets.env` from current environment variables:

```bash
TG_API_ID=123456 \
TG_API_HASH=your_hash \
TG_USER_SESSION=your_string_session \
CONTROL_BOT_TOKEN=your_control_bot_token \
tg-personal-bootstrap-secrets
```

## 3. Pick The Target Chat

```bash
tg-personal-list-dialogs --secrets secrets.env --limit 50
```

Copy the selected dialog ID into `config.toml` as `target_chat_id`.

Set `owner_telegram_id` to your own Telegram user ID. Only this ID can use the control bot.

## 4. Start The Service

```bash
tg-personal-assistant --config config.toml --secrets secrets.env
```

Open the control bot in Telegram and send `/start`.

## 5. Expected Behavior

- Text messages in the target chat get an auto-reply when auto mode is on.
- Voice messages can be transcribed and answered with voice if `GROQ_API_KEY`, `ffmpeg`, and `edge-tts` are available.
- Handoff trigger phrases do not get an automatic reply. The owner receives an alert instead.
- `/stop` in the target chat pauses automation until the owner resumes it.
- If the owner replies manually before the delayed auto-reply is sent, the pending auto-reply is cancelled.

## 6. Production Notes

- Keep `config.toml`, `secrets.env`, and `data/` private.
- Run only one instance per control bot token, otherwise Telegram polling will conflict.
- Keep Codex CLI authenticated on the server user that runs the service.
- Use `systemd`, Docker, or another process supervisor for long-running deployment.
- Back up only the SQLite database if you need session continuity; never publish it.
