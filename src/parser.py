from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import dateparser
import requests
from dateparser.search import search_dates

from .config import Settings

logger = logging.getLogger(__name__)


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
_RE_REMIND_IN = re.compile(r"напомни(?:ть)?\s+через\s+(.+)$", re.IGNORECASE)
_RE_DURATION_PART = re.compile(
    r"(\d+)\s*(минут|мин|час|часа|часов|день|дня|дней|неделю|недели|недель)",
    re.IGNORECASE,
)
_RE_DATE_HINT = re.compile(
    r"(\b\d{1,2}[.:]\d{2}\b|\b\d{1,2}\b|\bсегодня\b|\bзавтра\b|\bпослезавтра\b|"
    r"\bпонедельник\b|\bвторник\b|\bсреда\b|\bчетверг\b|\bпятница\b|\bсуббота\b|\bвоскресенье\b|"
    r"\bутра\b|\bднем\b|\bвечером\b|\bночью\b)",
    re.IGNORECASE,
)
_RE_TIME_TOKEN = re.compile(r"\b\d{1,2}([.:]\d{2})?\b")
_RE_DATE_WORD = re.compile(
    r"\b(сегодня|завтра|послезавтра|понедельник|вторник|среда|четверг|пятница|суббота|воскресенье)\b",
    re.IGNORECASE,
)
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
            logger.info("Perplexity parse success")
            return parsed
        logger.warning("Perplexity parse failed, fallback to local parser")
    return _parse_fallback(text, now, settings)


def _parse_with_perplexity(text: str, now: datetime, settings: Settings) -> ParsedTask | None:
    prompt = (
        "Ты — парсер задач для Telegram-бота. Твоя цель — понять смысл задачи и вернуть ТОЛЬКО JSON.\n"
        "JSON поля: title (строка), description (строка или null), due_at (ISO8601 или null), "
        "remind_at (ISO8601 или null), repeat_rule (строка или null).\n"
        "Timezone: Europe/Moscow. Если даты нет, верни null. "
        "repeat_rule: daily|weekly|monthly|yearly|every N days|every N weeks|none.\n"
        "Правила:\n"
        "- title: краткая суть задачи, без фраз 'напомни', 'через', дат/времени.\n"
        "- description: дополнительные детали, ссылки, упоминания (@), если они есть.\n"
        "- due_at: срок выполнения. Если срок не указан — null.\n"
        "- remind_at: когда напомнить. Если есть 'напомни через X' или 'через X' — вычисли время напоминания.\n"
        "- Если указано только 'напомни через X' без срока, due_at = remind_at.\n"
        "- Если указано 'напомни за X' и есть due_at — remind_at = due_at - X.\n"
        "- Если указано 'напомни в HH:MM' без даты — возьми ближайшее будущее время.\n"
        "- Если есть повтор (ежедневно/еженедельно/каждые N дней/недель) — заполни repeat_rule.\n"
        "Примеры:\n"
        "1) 'напомни через 2 часа и 20 минут зайти в вов' -> title:'зайти в вов', remind_at = now+2h20m, due_at=remind_at.\n"
        "2) 'созвон с клиентом завтра в 15:00 напомни за 1 час' -> title:'созвон с клиентом', due_at=завтра 15:00, remind_at=14:00.\n"
        "3) 'отчет каждую неделю в пятницу 18:00' -> title:'отчет', due_at=ближайшая пятница 18:00, repeat_rule='weekly'.\n"
        "4) 'выписать идеи' -> title:'выписать идеи', due_at=null, remind_at=null.\n"
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
        data = _safe_json_loads(content)
        if data is None:
            logger.warning("Perplexity returned non-JSON: %r", content[:500])
            return None
    except Exception:
        logger.exception("Perplexity request failed")
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
        logger.exception("Perplexity response parse failed")
        return None


def _parse_fallback(text: str, now: datetime, settings: Settings) -> ParsedTask:
    due_at = _extract_due_date(text, now, settings)
    remind_at = _extract_remind_at(text, now, settings, due_at)
    if remind_at and not due_at:
        due_at = remind_at
    if not remind_at and due_at and "напомни" in text.lower():
        remind_at = due_at
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
    combined = _extract_date_with_time(text, now, settings)
    if combined:
        return combined
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
        candidates = []
        for matched_text, dt in matches:
            if dt <= now:
                continue
            has_time = bool(
                re.search(r"\d{1,2}[.:]\d{2}|\b(утра|днем|вечером|ночью)\b", matched_text, re.IGNORECASE)
            )
            candidates.append((has_time, dt))
        if candidates:
            candidates.sort(key=lambda item: (not item[0], item[1]))
            return candidates[0][1]
    return None


def _extract_date_with_time(text: str, now: datetime, settings: Settings) -> datetime | None:
    if not _RE_DATE_HINT.search(text):
        return None
    date_word_match = _RE_DATE_WORD.search(text)
    if not date_word_match:
        return None
    time_match = _RE_TIME_TOKEN.search(text)
    if not time_match and not re.search(r"\b(утра|днем|вечером|ночью)\b", text, re.IGNORECASE):
        return None
    date_part = dateparser.parse(
        date_word_match.group(0),
        languages=["ru"],
        settings={
            "RELATIVE_BASE": now,
            "TIMEZONE": str(settings.tz),
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
        },
    )
    if not date_part:
        return None
    time_text = time_match.group(0) if time_match else "00:00"
    suffix_match = re.search(r"\b(утра|днем|вечером|ночью)\b", text, re.IGNORECASE)
    if suffix_match:
        time_text = f"{time_text} {suffix_match.group(0)}"
    time_part = dateparser.parse(
        time_text,
        languages=["ru"],
        settings={
            "RELATIVE_BASE": now,
            "TIMEZONE": str(settings.tz),
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
        },
    )
    if not time_part:
        return None
    try:
        return date_part.replace(
            hour=time_part.hour,
            minute=time_part.minute,
            second=0,
            microsecond=0,
        )
    except ValueError:
        return None


def _extract_remind_at(
    text: str, now: datetime, settings: Settings, due_at: datetime | None
) -> datetime | None:
    in_match = _RE_REMIND_IN.search(text)
    if in_match:
        delta = timedelta()
        for amount_text, unit in _RE_DURATION_PART.findall(in_match.group(1)):
            delta += _to_delta(int(amount_text), unit.lower())
        remind_at = now + delta if delta else None
        return remind_at if remind_at and remind_at > now else None

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
    text = _RE_REMIND_IN.sub("", text)
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


def _safe_json_loads(content: str) -> dict | None:
    try:
        return json.loads(content)
    except Exception:
        pass
    if "{" in content and "}" in content:
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            snippet = content[start : end + 1]
            try:
                return json.loads(snippet)
            except Exception:
                return None
    return None


def _nullify(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, str):
        return value.strip() or None
    return None
