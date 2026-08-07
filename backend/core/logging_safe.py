import re

_BOT_URL_RE = re.compile(r"(https?://api\.telegram\.org/bot)([^/\s]+)", re.IGNORECASE)

# postgresql://user:pass@host, postgresql+asyncpg://..., postgres://...
_DB_URL_RE = re.compile(
    r"((?:postgresql(?:\+\w+)?|postgres)://[^:/?\s]+):([^@/\s]+)@",
    re.IGNORECASE,
)


def redact_secrets(
    text: str,
    bot_token: str = "",
    *,
    webhook_secret: str = "",
    groq_api_key: str = "",
) -> str:
    """Strip secrets from exception text / URLs before logging or client responses."""
    redacted = _BOT_URL_RE.sub(r"\1[REDACTED]", text)
    redacted = _DB_URL_RE.sub(r"\1:[REDACTED]@", redacted)
    if bot_token:
        redacted = redacted.replace(bot_token, "[REDACTED]")
    if webhook_secret:
        redacted = redacted.replace(webhook_secret, "[REDACTED]")
    if groq_api_key:
        redacted = redacted.replace(groq_api_key, "[REDACTED]")
    return redacted
