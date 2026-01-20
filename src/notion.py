from __future__ import annotations

from datetime import datetime
import logging

import requests

from .config import Settings
from .database import Task

logger = logging.getLogger(__name__)

def sync_task_created(settings: Settings, task: Task) -> str | None:
    if not settings.notion_token:
        return None

    if settings.notion_db_id:
        payload = _build_database_payload(settings, task)
    elif settings.notion_page_id:
        return append_to_page(settings, settings.notion_page_id, task)
    else:
        return None

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
            return None
        return response.json().get("id")
    except Exception:
        logger.exception("Notion API request failed")
        return None


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
    if settings.notion_prop_done:
        payload["properties"][settings.notion_prop_done] = {"checkbox": False}
    if task.due_at and settings.notion_prop_due:
        payload["properties"][settings.notion_prop_due] = {
            "date": {"start": task.due_at.isoformat()}
        }
    if task.repeat_rule and settings.notion_prop_repeat:
        payload["properties"][settings.notion_prop_repeat] = {
            "rich_text": [{"text": {"content": task.repeat_rule}}]
        }
    return payload


def _build_page_children(task: Task) -> list[dict]:
    return [
        {
            "object": "block",
            "type": "to_do",
            "to_do": {
                "checked": False,
                "rich_text": [{"type": "text", "text": {"content": task.title}}],
            },
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [
                {"type": "text", "text": {"content": f"Срок: {task.due_at.isoformat() if task.due_at else '—'}"}}
            ]},
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [
                {"type": "text", "text": {"content": f"Напоминание: {task.remind_at.isoformat() if task.remind_at else '—'}"}}
            ]},
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [
                {"type": "text", "text": {"content": f"Повтор: {task.repeat_rule or '—'}"}}
            ]},
        },
    ]


def get_page(settings: Settings, page_id: str) -> dict | None:
    try:
        response = requests.get(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers={
                "Authorization": f"Bearer {settings.notion_token}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        if response.status_code >= 400:
            logger.error("Notion API error %s: %s", response.status_code, response.text)
            return None
        return response.json()
    except Exception:
        logger.exception("Notion API request failed")
        return None


def append_to_page(settings: Settings, page_id: str, task: Task) -> str | None:
    try:
        response = requests.patch(
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            json={"children": _build_page_children(task)},
            headers={
                "Authorization": f"Bearer {settings.notion_token}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        if response.status_code >= 400:
            logger.error("Notion API error %s: %s", response.status_code, response.text)
            return None
        results = response.json().get("results", [])
        if results:
            return results[0].get("id")
        return None
    except Exception:
        logger.exception("Notion API request failed")
        return None


def get_block(settings: Settings, block_id: str) -> dict | None:
    try:
        response = requests.get(
            f"https://api.notion.com/v1/blocks/{block_id}",
            headers={
                "Authorization": f"Bearer {settings.notion_token}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        if response.status_code >= 400:
            logger.error("Notion API error %s: %s", response.status_code, response.text)
            return None
        return response.json()
    except Exception:
        logger.exception("Notion API request failed")
        return None


def archive_page(settings: Settings, page_id: str) -> bool:
    try:
        response = requests.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            json={"archived": True},
            headers={
                "Authorization": f"Bearer {settings.notion_token}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        if response.status_code >= 400:
            logger.error("Notion API error %s: %s", response.status_code, response.text)
            return False
        return True
    except Exception:
        logger.exception("Notion API request failed")
        return False


def archive_block(settings: Settings, block_id: str) -> bool:
    try:
        response = requests.patch(
            f"https://api.notion.com/v1/blocks/{block_id}",
            json={"archived": True},
            headers={
                "Authorization": f"Bearer {settings.notion_token}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        if response.status_code >= 400:
            logger.error("Notion API error %s: %s", response.status_code, response.text)
            return False
        return True
    except Exception:
        logger.exception("Notion API request failed")
        return False


def format_date(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
