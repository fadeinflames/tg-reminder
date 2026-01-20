from __future__ import annotations

from datetime import datetime
import logging

import requests

from .config import Settings
from .database import Task

logger = logging.getLogger(__name__)

def sync_task_created(settings: Settings, task: Task) -> None:
    if not settings.notion_token:
        return

    if settings.notion_db_id:
        payload = _build_database_payload(settings, task)
    elif settings.notion_page_id:
        payload = _build_page_payload(settings, task)
    else:
        return

    try:
        response = requests.post(
            "https://api.notion.com/v1/pages",
            json=payload,
            headers={
                "Authorization": f"Bearer {settings.notion_token}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        if response.status_code >= 400:
            logger.error(
                "Notion API error %s: %s",
                response.status_code,
                response.text,
            )
    except Exception:
        logger.exception("Notion API request failed")


def _build_database_payload(settings: Settings, task: Task) -> dict:
    payload = {
        "parent": {"database_id": settings.notion_db_id},
        "properties": {
            settings.notion_prop_name: {
                "title": [{"text": {"content": task.title}}]
            },
        },
    }
    if settings.notion_prop_status and settings.notion_status_value:
        payload["properties"][settings.notion_prop_status] = {
            "select": {"name": settings.notion_status_value}
        }
    if task.due_at and settings.notion_prop_due:
        payload["properties"][settings.notion_prop_due] = {
            "date": {"start": task.due_at.isoformat()}
        }
    if task.repeat_rule and settings.notion_prop_repeat:
        payload["properties"][settings.notion_prop_repeat] = {
            "rich_text": [{"text": {"content": task.repeat_rule}}]
        }
    return payload


def _build_page_payload(settings: Settings, task: Task) -> dict:
    children = [
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
            {"type": "text", "text": {"content": f"Срок: {task.due_at.isoformat() if task.due_at else '—'}"}}
        ]}},
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
            {"type": "text", "text": {"content": f"Напоминание: {task.remind_at.isoformat() if task.remind_at else '—'}"}}
        ]}},
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
            {"type": "text", "text": {"content": f"Повтор: {task.repeat_rule or '—'}"}}
        ]}},
    ]
    return {
        "parent": {"page_id": settings.notion_page_id},
        "properties": {
            "title": {"title": [{"text": {"content": task.title}}]},
        },
        "children": children,
    }


def format_date(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
