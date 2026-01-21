from __future__ import annotations

import logging
from datetime import datetime, time, timedelta

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .config import Settings, load_settings
from .database import Task, create_task, get_task, init_db, list_future_reminders
from .database import (
    list_chat_ids_with_open_tasks,
    list_tasks_for_chat,
    list_tasks_with_notion,
    update_task_notion_id,
    update_task_status,
)
from .notion import archive_block, archive_page, get_block, get_page, sync_task_created
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
    await update.message.reply_text(
        "Привет! Я бот напоминаний.\n"
        "Просто напиши задачу обычным сообщением.\n"
        "Пример: Купить молоко завтра в 18:00 напомни за 2 часа"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        await _deny(update)
        return
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
    db_path: str = context.bot_data["db_path"]
    await sync_closed_tasks(context)
    chat_id = update.effective_chat.id if update.effective_chat else None
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
    await sync_closed_tasks(context)
    await update.message.reply_text("✅ Синхронизация завершена.")


async def capture_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        await _deny(update)
        return
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
    settings: Settings = context.bot_data["settings"]
    db_path: str = context.bot_data["db_path"]
    for task in list_tasks_with_notion(db_path):
        if not task.notion_page_id:
            continue
        if _is_task_closed_in_notion(settings, task.notion_page_id):
            if settings.notion_page_id:
                archived = archive_block(settings, task.notion_page_id)
            else:
                archived = archive_page(settings, task.notion_page_id)
            if archived:
                update_task_status(db_path, task.id, "done", datetime.utcnow())
                remove_reminder(context.application, task.id)


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
