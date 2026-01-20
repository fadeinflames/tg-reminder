from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import dateparser
import requests
from dateparser.search import search_dates

from .config import Settings


@dataclass
class ParsedTask:
    title: str
    description: str | None
    due_at: datetime | None
    remind_at: datetime | None
    repeat_rule: str | None


_RE_REMIND_OFFSET = re.compile(
    r"напомни(?:ть)?\s+за\s+(\d+)\s*(минут|мин|час|часа|часов|день|дня|дней|неделю|недели|недель)",
    re.IGNORECASE,
)
_RE_REMIND_AT = re.compile(r"напомни(?:ть)?\s+в\s+(.+)$", re.IGNORECASE)
_RE_REPEAT_DAILY = re.compile(r"(каждый день|ежедневно)", re.IGNORECASE)
_RE_REPEAT_WEEKLY = re.compile(r"(каждую неделю|еженедельно)", re.IGNORECASE)
_RE_REPEAT_MONTHLY = re.compile(r"(каждый месяц|ежемесячно)", re.IGNORECASE)
_RE_REPEAT_YEARLY = re.compile(r"(каждый год|ежегодно)", re.IGNORECASE)
_RE_REPEAT_EVERY = re.compile(r"каждые?\s+(\d+)\s*(день|дня|дней|неделю|недели|недель)", re.IGNORECASE)


def parse_task_text(text: str, now: datetime, settings: Settings) -> ParsedTask:
    text = text.strip()
    if settings.perplexity_api_key:
        parsed = _parse_with_perplexity(text, now, settings)
        if parsed:
            return parsed
    return _parse_fallback(text, now, settings)


def _parse_with_perplexity(text: str, now: datetime, settings: Settings) -> ParsedTask | None:
    prompt = (
        "Из текста задачи извлеки поля. Верни ТОЛЬКО JSON.\n"
        "Поля: title (строка), description (строка или null), due_at (ISO8601 или null), "
        "remind_at (ISO8601 или null), repeat_rule (строка или null).\n"
        "Timezone: Europe/Moscow. Если даты нет, верни null. "
        "repeat_rule: daily|weekly|monthly|yearly|every N days|every N weeks|none.\n"
        f"Сейчас: {now.isoformat()}.\n"
        f"Текст: {text}\n"
        "JSON:"
    )

    headers = {
        "Authorization": f"Bearer {settings.perplexity_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "sonar",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
    }

    try:
        response = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers=headers,
            json=payload,
            timeout=20,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        data = json.loads(content)
    except Exception:
        return None

    try:
        return ParsedTask(
            title=str(data.get("title") or text),
            description=_nullify(data.get("description")),
            due_at=_parse_dt(data.get("due_at")),
            remind_at=_parse_dt(data.get("remind_at")),
            repeat_rule=_normalize_repeat(data.get("repeat_rule")),
        )
    except Exception:
        return None


def _parse_fallback(text: str, now: datetime, settings: Settings) -> ParsedTask:
    due_at = _extract_due_date(text, now, settings)
    remind_at = _extract_remind_at(text, now, settings, due_at)
    repeat_rule = _extract_repeat_rule(text)
    title = _cleanup_title(text)

    return ParsedTask(
        title=title,
        description=None,
        due_at=due_at,
        remind_at=remind_at,
        repeat_rule=repeat_rule,
    )


def _extract_due_date(text: str, now: datetime, settings: Settings) -> datetime | None:
    matches = search_dates(
        text,
        languages=["ru"],
        settings={
            "RELATIVE_BASE": now,
            "TIMEZONE": str(settings.tz),
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
        },
    )
    if matches:
        return matches[0][1]
    return None


def _extract_remind_at(
    text: str, now: datetime, settings: Settings, due_at: datetime | None
) -> datetime | None:
    offset_match = _RE_REMIND_OFFSET.search(text)
    if offset_match and due_at:
        amount = int(offset_match.group(1))
        unit = offset_match.group(2).lower()
        delta = _to_delta(amount, unit)
        remind_at = due_at - delta
        return remind_at if remind_at > now else None

    at_match = _RE_REMIND_AT.search(text)
    if at_match:
        parsed = dateparser.parse(
            at_match.group(1),
            languages=["ru"],
            settings={
                "RELATIVE_BASE": now,
                "TIMEZONE": str(settings.tz),
                "RETURN_AS_TIMEZONE_AWARE": True,
                "PREFER_DATES_FROM": "future",
            },
        )
        return parsed if parsed and parsed > now else None
    return None


def _to_delta(amount: int, unit: str) -> timedelta:
    if unit.startswith("мин"):
        return timedelta(minutes=amount)
    if unit.startswith("час"):
        return timedelta(hours=amount)
    if unit.startswith("нед"):
        return timedelta(weeks=amount)
    return timedelta(days=amount)


def _extract_repeat_rule(text: str) -> str | None:
    if _RE_REPEAT_DAILY.search(text):
        return "daily"
    if _RE_REPEAT_WEEKLY.search(text):
        return "weekly"
    if _RE_REPEAT_MONTHLY.search(text):
        return "monthly"
    if _RE_REPEAT_YEARLY.search(text):
        return "yearly"

    match = _RE_REPEAT_EVERY.search(text)
    if match:
        amount = int(match.group(1))
        unit = match.group(2).lower()
        if unit.startswith("нед"):
            return f"every {amount} weeks"
        return f"every {amount} days"
    return None


def _cleanup_title(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = _RE_REMIND_OFFSET.sub("", text)
    text = _RE_REMIND_AT.sub("", text)
    text = _RE_REPEAT_DAILY.sub("", text)
    text = _RE_REPEAT_WEEKLY.sub("", text)
    text = _RE_REPEAT_MONTHLY.sub("", text)
    text = _RE_REPEAT_YEARLY.sub("", text)
    text = _RE_REPEAT_EVERY.sub("", text)
    return re.sub(r"\s+", " ", text).strip() or "Без названия"


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _normalize_repeat(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, str):
        value = value.strip().lower()
        if value in {"none", "null"}:
            return None
        return value
    return None


def _nullify(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, str):
        return value.strip() or None
    return None
