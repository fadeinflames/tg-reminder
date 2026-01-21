from __future__ import annotations

import logging
from datetime import datetime, time, timedelta

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .config import Settings, load_settings
from .database import Task, create_task, get_task, init_db, list_future_reminders
from .database import (
    clear_tasks,
    list_chat_ids_with_open_tasks,
    list_tasks_for_chat,
    list_tasks_with_notion_all,
    update_task_due_at,
    update_task_notion_id,
    update_task_status,
    update_task_title,
)
from .notion import (
    archive_page,
    get_block,
    get_page,
    list_page_children,
    query_database,
    sync_task_created,
)
from .parser import parse_task_text
from .utils import format_dt


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


def _is_allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    settings: Settings = context.bot_data["settings"]
    user = update.effective_user
    if not user:
        return False
    return user.id in settings.allowed_user_ids


async def _deny(update: Update) -> None:
    if update.message:
        await update.message.reply_text("Нет доступа.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        await _deny(update)
        return
    _remember_chat(update, context)
    await update.message.reply_text(
        "Привет! Я бот напоминаний.\n"
        "Просто напиши задачу обычным сообщением.\n"
        "Пример: Купить молоко завтра в 18:00 напомни за 2 часа"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        await _deny(update)
        return
    _remember_chat(update, context)
    await update.message.reply_text(
        "Просто отправь сообщение, и оно станет задачей в Notion.\n"
        "Пример: Созвон с клиентом завтра 15:00 напомни за 1 час\n"
        "Повторы: ежедневно, еженедельно, каждые 3 дня\n"
        "Команды: /list (список), /sync (синхронизация)"
    )


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        await _deny(update)
        return
    _remember_chat(update, context)
    db_path: str = context.bot_data["db_path"]
    chat_id = update.effective_chat.id if update.effective_chat else None
    user_id = update.effective_user.id if update.effective_user else None
    await sync_tasks_from_notion(context, chat_id=chat_id, user_id=user_id, reset=True)
    if not chat_id:
        return
    tasks = list_tasks_for_chat(db_path, chat_id, status="open")
    if not tasks:
        await update.message.reply_text("Открытых задач нет.")
        return
    lines = [f"📋 Открытые задачи ({len(tasks)}):"]
    lines.extend(_format_task_lines(tasks))
    await update.message.reply_text("\n".join(lines))


async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        await _deny(update)
        return
    _remember_chat(update, context)
    chat_id = update.effective_chat.id if update.effective_chat else None
    user_id = update.effective_user.id if update.effective_user else None
    await sync_tasks_from_notion(context, chat_id=chat_id, user_id=user_id, reset=True)
    await update.message.reply_text("✅ Синхронизация завершена.")


async def capture_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        await _deny(update)
        return
    _remember_chat(update, context)
    if update.effective_user and update.effective_user.is_bot:
        return
    settings: Settings = context.bot_data["settings"]
    db_path: str = context.bot_data["db_path"]
    text = (update.message.text or "").strip()
    if not text:
        return

    now = datetime.now(settings.tz)
    parsed = parse_task_text(text, now, settings)
    task = Task(
        id=None,
        user_id=update.effective_user.id,
        chat_id=update.effective_chat.id,
        title=parsed.title,
        description=parsed.description,
        due_at=parsed.due_at,
        remind_at=parsed.remind_at,
        repeat_rule=parsed.repeat_rule,
        notion_page_id=None,
        status="open",
        created_at=now,
        updated_at=now,
    )
    task_id = create_task(db_path, task)
    task.id = task_id

    if task.remind_at:
        schedule_reminder(context.application, task)

    notion_id = sync_task_created(settings, task)
    if notion_id:
        update_task_notion_id(db_path, task_id, notion_id)

    await update.message.reply_text(
        f"✅ Добавлено в Notion #{task_id}\n"
        f"Текст: {task.title}\n"
        f"Срок: {format_dt(task.due_at)}\n"
        f"Напоминание: {format_dt(task.remind_at)}\n"
        f"Повтор: {task.repeat_rule or '—'}"
    )


async def reminder_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    db_path: str = context.bot_data["db_path"]
    settings: Settings = context.bot_data["settings"]
    job_data = context.job.data or {}
    task_id = job_data.get("task_id")
    chat_id = job_data.get("chat_id")
    if not task_id or not chat_id:
        return
    task = get_task(db_path, task_id)
    if not task or task.status != "open":
        return
    if task.notion_page_id and settings.notion_token:
        if _is_task_closed_in_notion(settings, task.notion_page_id):
            update_task_status(db_path, task.id, "done", datetime.utcnow())
            remove_reminder(context.application, task.id)
            return
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"🔔 Напоминание по задаче #{task.id}\n"
            f"{task.title}\n"
            f"Срок: {format_dt(task.due_at)}\n"
            f"Выполнить: /done {task.id}"
        ),
    )


def schedule_reminder(app: Application, task: Task) -> None:
    if not task.remind_at:
        return
    name = f"remind_{task.id}"
    for job in app.job_queue.get_jobs_by_name(name):
        job.schedule_removal()
    app.job_queue.run_once(
        reminder_callback,
        when=task.remind_at,
        name=name,
        data={"task_id": task.id, "chat_id": task.chat_id},
    )


def remove_reminder(app: Application, task_id: int) -> None:
    name = f"remind_{task_id}"
    for job in app.job_queue.get_jobs_by_name(name):
        job.schedule_removal()


async def daily_summary(context: ContextTypes.DEFAULT_TYPE) -> None:
    db_path: str = context.bot_data["db_path"]
    settings: Settings = context.bot_data["settings"]
    now = datetime.now(settings.tz)
    today = now.date()
    chat_ids = list_chat_ids_with_open_tasks(db_path)
    for chat_id in chat_ids:
        tasks = list_tasks_for_chat(db_path, chat_id, status="open")
        if not tasks:
            continue
        overdue: list[Task] = []
        today_tasks: list[Task] = []
        upcoming: list[Task] = []
        no_due: list[Task] = []
        for task in tasks:
            if not task.due_at:
                no_due.append(task)
                continue
            if task.due_at.date() < today:
                overdue.append(task)
            elif task.due_at.date() == today:
                today_tasks.append(task)
            else:
                upcoming.append(task)

        lines = [f"📋 Список задач ({len(tasks)}):"]
        if overdue:
            lines.append("Просроченные:")
            lines.extend(_format_task_lines(overdue))
        if today_tasks:
            lines.append("Сегодня:")
            lines.extend(_format_task_lines(today_tasks))
        if upcoming:
            lines.append("Скоро:")
            lines.extend(_format_task_lines(upcoming))
        if no_due:
            lines.append("Без срока:")
            lines.extend(_format_task_lines(no_due))
        await context.bot.send_message(chat_id=chat_id, text="\n".join(lines))


async def sync_closed_tasks(context: ContextTypes.DEFAULT_TYPE) -> None:
    await sync_tasks_from_notion(context, reset=True)


async def sync_tasks_from_notion(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat_id: int | None = None,
    user_id: int | None = None,
    reset: bool = False,
) -> None:
    settings: Settings = context.bot_data["settings"]
    db_path: str = context.bot_data["db_path"]
    if not settings.notion_token:
        return
    chat_id, user_id = _resolve_primary_ids(context, chat_id, user_id)
    if reset:
        if not chat_id or not user_id:
            logger.warning("Skip sync reset: chat_id or user_id is missing")
            return
        _remove_all_reminders(context.application)
        clear_tasks(db_path)
    tasks_by_notion = {
        task.notion_page_id: task
        for task in list_tasks_with_notion_all(db_path)
        if task.notion_page_id
    }
    now = datetime.now(settings.tz)
    if settings.notion_db_id:
        pages = query_database(settings, settings.notion_db_id)
        pages_by_id = {page.get("id"): page for page in pages if page.get("id")}
        for task in tasks_by_notion.values():
            if task.notion_page_id not in pages_by_id and task.status != "done":
                update_task_status(db_path, task.id, "done", datetime.utcnow())
                remove_reminder(context.application, task.id)
        for page in pages:
            page_id = page.get("id")
            if not page_id:
                continue
            title = _extract_page_title(page, settings)
            due_at = _extract_page_due(page, settings)
            done = _is_page_done(page, settings)
            status = "done" if done else "open"
            task = tasks_by_notion.get(page_id)
            if not task:
                new_task = Task(
                    id=None,
                    user_id=user_id,
                    chat_id=chat_id,
                    title=title or "Без названия",
                    description=None,
                    due_at=due_at,
                    remind_at=None,
                    repeat_rule=None,
                    notion_page_id=page_id,
                    status=status,
                    created_at=now,
                    updated_at=now,
                )
                create_task(db_path, new_task)
                continue
            else:
                if task.status != status:
                    update_task_status(db_path, task.id, status, datetime.utcnow())
                    if status == "done":
                        remove_reminder(context.application, task.id)
                    elif task.remind_at and task.remind_at > now:
                        schedule_reminder(context.application, task)
                if title and title != task.title:
                    update_task_title(db_path, task.id, title)
                if due_at != task.due_at:
                    update_task_due_at(db_path, task.id, due_at)
        return
    if not settings.notion_page_id:
        return
    blocks = list_page_children(settings, settings.notion_page_id)
    todo_blocks = {
        block.get("id"): block
        for block in blocks
        if block.get("type") == "to_do"
    }
    for task in tasks_by_notion.values():
        if task.notion_page_id not in todo_blocks and task.status != "done":
            update_task_status(db_path, task.id, "done", datetime.utcnow())
            remove_reminder(context.application, task.id)
    for block_id, block in todo_blocks.items():
        todo = block.get("to_do", {})
        checked = bool(todo.get("checked"))
        status = "done" if checked else "open"
        title = _extract_todo_title(todo)
        task = tasks_by_notion.get(block_id)
        if not task:
            new_task = Task(
                id=None,
                user_id=user_id,
                chat_id=chat_id,
                title=title or "Без названия",
                description=None,
                due_at=None,
                remind_at=None,
                repeat_rule=None,
                notion_page_id=block_id,
                status=status,
                created_at=now,
                updated_at=now,
            )
            create_task(db_path, new_task)
            continue
        if task.status != status:
            update_task_status(db_path, task.id, status, datetime.utcnow())
            if status == "done":
                remove_reminder(context.application, task.id)
            elif task.remind_at and task.remind_at > now:
                schedule_reminder(context.application, task)
        if title and title != task.title:
            update_task_title(db_path, task.id, title)


def _extract_todo_title(todo: dict) -> str:
    parts = []
    for item in todo.get("rich_text", []) or []:
        if "plain_text" in item:
            parts.append(item["plain_text"])
        else:
            text = item.get("text", {}).get("content")
            if text:
                parts.append(text)
    return " ".join(" ".join(parts).split()).strip()


def _remember_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat:
        context.bot_data["primary_chat_id"] = update.effective_chat.id
    if update.effective_user:
        context.bot_data["primary_user_id"] = update.effective_user.id


def _resolve_primary_ids(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int | None,
    user_id: int | None,
) -> tuple[int | None, int | None]:
    if not chat_id:
        chat_id = context.bot_data.get("primary_chat_id")
    if not user_id:
        user_id = context.bot_data.get("primary_user_id")
    return chat_id, user_id


def _remove_all_reminders(app: Application) -> None:
    for job in list(app.job_queue.jobs()):
        if job.name and job.name.startswith("remind_"):
            job.schedule_removal()


def _extract_page_title(page: dict, settings: Settings) -> str:
    prop = page.get("properties", {}).get(settings.notion_prop_name, {})
    parts = []
    for item in prop.get("title", []) or []:
        if "plain_text" in item:
            parts.append(item["plain_text"])
        else:
            text = item.get("text", {}).get("content")
            if text:
                parts.append(text)
    return " ".join(" ".join(parts).split()).strip()


def _extract_page_due(page: dict, settings: Settings) -> datetime | None:
    if not settings.notion_prop_due:
        return None
    prop = page.get("properties", {}).get(settings.notion_prop_due, {})
    date = prop.get("date", {}) if isinstance(prop, dict) else {}
    start = date.get("start")
    if not start:
        return None
    value = start.replace("Z", "+00:00")
    try:
        if "T" not in value:
            value = f"{value}T00:00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _is_page_done(page: dict, settings: Settings) -> bool:
    if page.get("archived"):
        return True
    if settings.notion_prop_done:
        prop = page.get("properties", {}).get(settings.notion_prop_done)
        if isinstance(prop, dict):
            return bool(prop.get("checkbox"))
    if settings.notion_prop_status and settings.notion_status_value:
        prop = page.get("properties", {}).get(settings.notion_prop_status)
        if isinstance(prop, dict):
            selected = prop.get("select") or {}
            name = selected.get("name")
            if name:
                return name != settings.notion_status_value
    return False


def _is_task_closed_in_notion(settings: Settings, page_id: str) -> bool:
    if settings.notion_db_id and settings.notion_prop_done:
        page = get_page(settings, page_id)
        if not page:
            return False
        prop = page.get("properties", {}).get(settings.notion_prop_done)
        if not prop:
            return False
        return bool(prop.get("checkbox"))

    block = get_block(settings, page_id)
    if not block:
        return False
    if block.get("type") != "to_do":
        return False
    to_do = block.get("to_do", {})
    return bool(to_do.get("checked"))




async def on_startup(app: Application) -> None:
    settings: Settings = app.bot_data["settings"]
    db_path: str = app.bot_data["db_path"]
    now = datetime.now(settings.tz)
    for task in list_future_reminders(db_path, now):
        schedule_reminder(app, task)
    for hour in (10, 15, 19):
        app.job_queue.run_daily(
            daily_summary,
            time(hour=hour, minute=0, tzinfo=settings.tz),
            name=f"daily_summary_{hour}",
        )
        app.job_queue.run_daily(
            sync_closed_tasks,
            time(hour=hour, minute=0, tzinfo=settings.tz),
            name=f"sync_full_{hour}",
        )
    app.job_queue.run_repeating(
        sync_closed_tasks,
        interval=timedelta(minutes=settings.sync_interval_minutes),
        first=timedelta(minutes=min(2, settings.sync_interval_minutes)),
        name="sync_closed_tasks",
    )


def _format_task_lines(tasks: list[Task]) -> list[str]:
    lines = []
    for task in tasks:
        title = " ".join(task.title.split())
        lines.append(
            f"• {title} | срок: {format_dt(task.due_at)} | "
            f"напомнить: {format_dt(task.remind_at)}"
        )
    return lines


def main() -> None:
    load_dotenv()
    settings = load_settings()
    init_db(settings.db_path)

    application = (
        Application.builder()
        .token(settings.bot_token)
        .post_init(on_startup)
        .build()
    )
    application.bot_data["settings"] = settings
    application.bot_data["db_path"] = settings.db_path

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("sync", sync_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, capture_message)
    )
    application.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
