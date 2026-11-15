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


## 7. Update the existing blitz.cloud app

1. In Control Bot, send `/auto_off` and verify that automation is stopped. Back
   up the app SQLite database and the Claude session database privately. Check
   that the directories containing the current `SQLITE_DB_PATH` and
   `CLAUDE_SESSION_DB` are listed as persistent in the app's Settings → Files.
   If they are not persistent, resolve that before relying on restart recovery.
2. Copy the reviewed code changes into the source repository already connected
   to the app. Keep runtime configuration, credentials, and SQLite files out of
   the commit. Keep the current startup command and environment settings.
3. Push to the branch followed by the **existing** app. A connected GitHub app
   builds on push. For a manually refreshed GitHub source, open the existing
   app's **Settings → Get the newest code**. Do not run a second copy with the
   same control token or Telegram session. If an old poller is still active,
   stop it and verify the new deployment starts without a polling conflict.
4. Check the deployment's Activity and logs, then use `/status` and
   `/autonomous_status` in Control Bot. The new SQLite table is created on
   startup. No manual migration is required. If necessary, restore the previous
   build from **Activity → Put this version back**.
5. Use `/autonomous_start [задача]` only when you intend to contact the already
   configured chat. The first reply identifies the AI. Later turns need a new
   incoming text. `/autonomous_stop` and `/auto_off` stop the session.
   `/resume` clears a pause, and a stopped session needs a fresh start.
   Voice auto-replies are still available in the regular reactive mode.
6. If status reports an uncertain delivery, review the target chat first.
   `/delivery_reviewed` closes that operation without retrying it. Start a new
   session explicitly if you want to continue. An outbound request already
   accepted by Telegram cannot be recalled by the stop command.

See the platform guides for [deploying from GitHub](https://blitz.cloud/docs/deploy-from-github/)
and [persistent files](https://blitz.cloud/docs/settings-and-files/).
