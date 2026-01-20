from __future__ import annotations

from datetime import datetime

import requests

from .config import Settings
from .database import Task


def sync_task_created(settings: Settings, task: Task) -> None:
    if not settings.notion_token or not settings.notion_db_id:
        return

    payload = {
        "parent": {"database_id": settings.notion_db_id},
        "properties": {
            "Name": {"title": [{"text": {"content": task.title}}]},
            "Status": {"select": {"name": "Open"}},
        },
    }
    if task.due_at:
        payload["properties"]["Due"] = {"date": {"start": task.due_at.isoformat()}}
    if task.repeat_rule:
        payload["properties"]["Repeat"] = {"rich_text": [{"text": {"content": task.repeat_rule}}]}

    try:
        requests.post(
            "https://api.notion.com/v1/pages",
            json=payload,
            headers={
                "Authorization": f"Bearer {settings.notion_token}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
    except Exception:
        return


def format_date(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
