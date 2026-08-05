import re

_BOT_URL_RE = re.compile(r"(https?://api\.telegram\.org/bot)([^/\s]+)", re.IGNORECASE)


def redact_secrets(text: str, bot_token: str = "") -> str:
    """Strip bot tokens from exception text / URLs before logging."""
    redacted = _BOT_URL_RE.sub(r"\1[REDACTED]", text)
    if bot_token:
        redacted = redacted.replace(bot_token, "[REDACTED]")
    return redacted
