from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    debug: bool = False
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    database_url: str = "postgresql+asyncpg://booking:booking@localhost:5432/booking"

    bot_token: str = ""
    webapp_url: str = "https://example.com"
    webhook_path: str = "/telegram/webhook"
    webhook_secret: str = ""
    public_base_url: str = ""

    reminder_minutes_before: int = 10
    initdata_max_age_seconds: int = 300

    # Booking business rules (Tier 1 fixed)
    min_duration_minutes: int = 15
    max_duration_minutes: int = 240
    slot_step_minutes: int = 30
    soon_free_minutes: int = 30

    # Office hours (Tier 2) — wall-clock in OFFICE_TIMEZONE
    office_timezone: str = "Europe/Moscow"
    office_hours_start: int = 9
    office_hours_end: int = 18

    # Tier 3 (optional NL booking) — empty = /book disabled, app still boots
    groq_api_key: str = ""

    # Access: bootstrap first admin when users table is empty (0 = disabled)
    bootstrap_admin_telegram_id: int = 0

    # No-show auto-cancel
    no_show_enabled: bool = True
    no_show_window_minutes: int = 10

    # Recurring bookings
    max_recurring_weeks: int = 8

    # Optional error monitoring
    sentry_dsn: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
