from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Settings:
    bot_token: str
    db_path: str
    tz: ZoneInfo
    perplexity_api_key: str | None
    allowed_user_ids: set[int]


def load_settings() -> Settings:
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise ValueError("BOT_TOKEN is required")

    db_path = os.getenv("DB_PATH", "data/reminder.db").strip()
    tz_name = os.getenv("BOT_TZ", "Europe/Moscow").strip()
    tz = ZoneInfo(tz_name)

    perplexity_api_key = os.getenv("PERPLEXITY_API_KEY", "").strip() or None
    allowed_raw = os.getenv("ALLOWED_USER_IDS", "").strip()
    if not allowed_raw:
        raise ValueError("ALLOWED_USER_IDS is required")
    allowed_user_ids = {
        int(part.strip())
        for part in allowed_raw.split(",")
        if part.strip().isdigit()
    }
    if not allowed_user_ids:
        raise ValueError("ALLOWED_USER_IDS must contain at least one user id")

    return Settings(
        bot_token=bot_token,
        db_path=db_path,
        tz=tz,
        perplexity_api_key=perplexity_api_key,
        allowed_user_ids=allowed_user_ids,
    )
