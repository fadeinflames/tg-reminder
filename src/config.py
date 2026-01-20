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
    notion_token: str | None
    notion_db_id: str | None
    notion_page_id: str | None
    allowed_user_ids: set[int]
    notion_prop_name: str
    notion_prop_status: str | None
    notion_prop_due: str | None
    notion_prop_repeat: str | None
    notion_status_value: str | None


def load_settings() -> Settings:
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise ValueError("BOT_TOKEN is required")

    db_path = os.getenv("DB_PATH", "data/reminder.db").strip()
    tz_name = os.getenv("BOT_TZ", "Europe/Moscow").strip()
    tz = ZoneInfo(tz_name)

    perplexity_api_key = os.getenv("PERPLEXITY_API_KEY", "").strip() or None
    notion_token = os.getenv("NOTION_TOKEN", "").strip() or None
    notion_db_id = os.getenv("NOTION_DB_ID", "").strip() or None
    notion_page_id = os.getenv("NOTION_PAGE_ID", "").strip() or None

    notion_prop_name = os.getenv("NOTION_PROP_NAME", "Name").strip() or "Name"
    notion_prop_status = os.getenv("NOTION_PROP_STATUS", "Status").strip() or None
    notion_prop_due = os.getenv("NOTION_PROP_DUE", "Due").strip() or None
    notion_prop_repeat = os.getenv("NOTION_PROP_REPEAT", "Repeat").strip() or None
    notion_status_value = os.getenv("NOTION_STATUS_VALUE", "Open").strip() or None

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

    if not notion_token:
        raise ValueError("NOTION_TOKEN is required")
    if not notion_db_id and not notion_page_id:
        raise ValueError("NOTION_DB_ID or NOTION_PAGE_ID is required")

    return Settings(
        bot_token=bot_token,
        db_path=db_path,
        tz=tz,
        perplexity_api_key=perplexity_api_key,
        notion_token=notion_token,
        notion_db_id=notion_db_id,
        notion_page_id=notion_page_id,
        allowed_user_ids=allowed_user_ids,
        notion_prop_name=notion_prop_name,
        notion_prop_status=notion_prop_status,
        notion_prop_due=notion_prop_due,
        notion_prop_repeat=notion_prop_repeat,
        notion_status_value=notion_status_value,
    )
