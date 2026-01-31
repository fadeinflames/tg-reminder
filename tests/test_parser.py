from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.bot import _normalize_parsed_dates
from src.config import Settings
from src.parser import ParsedTask, parse_task_text


def _settings() -> Settings:
    return Settings(
        bot_token="test",
        db_path=":memory:",
        tz=ZoneInfo("Europe/Moscow"),
        perplexity_api_key=None,
        allowed_user_ids={354573537},
    )


def test_parse_task_text_due_and_remind():
    settings = _settings()
    now = datetime(2026, 2, 1, 12, 0, tzinfo=settings.tz)
    text = "созвон с клиентом завтра в 15:00 напомни за 1 час"
    parsed = parse_task_text(text, now, settings)
    assert parsed.due_at is not None
    assert parsed.remind_at is not None
    assert parsed.due_at.date() == (now.date() + timedelta(days=1))
    assert parsed.due_at.hour == 15
    assert parsed.remind_at == parsed.due_at - timedelta(hours=1)


def test_normalize_parsed_dates_rolls_forward_repeat():
    settings = _settings()
    now = datetime(2026, 2, 1, 12, 0, tzinfo=settings.tz)
    due_at = now - timedelta(days=2)
    parsed = ParsedTask(
        title="task",
        description=None,
        due_at=due_at,
        remind_at=due_at - timedelta(hours=1),
        repeat_rule="daily",
    )
    new_due, new_remind = _normalize_parsed_dates(parsed, now)
    assert new_due is not None
    assert new_due > now
    assert new_remind == new_due - timedelta(hours=1)
