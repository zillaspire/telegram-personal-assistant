# Security Policy

## Supported Use

Telegram Personal Assistant is intended for consent-based, one-to-one Telegram automation where all people involved understand that automation may be used.

Do not use it for impersonation, spam, harassment, surveillance, or mass outreach.

## Secrets

Never commit:

- `secrets.env`
- `config.toml`
- Telethon session strings
- Telegram bot tokens
- API keys
- SQLite runtime data from `data/`

If a secret is accidentally committed, rotate it immediately. Removing it from the latest commit is not enough if it has already been pushed.

## Reporting Issues

If you find a security issue, please open a private security advisory on GitHub if available, or contact the repository owner privately. Avoid posting working exploit details in a public issue.
