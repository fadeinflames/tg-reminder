from __future__ import annotations

import logging
from datetime import datetime

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .config import Settings, load_settings
from .database import Task, create_task, get_task, init_db, list_future_reminders
from .notion import sync_task_created
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
        "Повторы: ежедневно, еженедельно, каждые 3 дня"
    )


async def capture_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        await _deny(update)
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
        status="open",
        created_at=now,
        updated_at=now,
    )
    task_id = create_task(db_path, task)
    task.id = task_id

    if task.remind_at:
        schedule_reminder(context.application, task)

    sync_task_created(settings, task)

    await update.message.reply_text(
        f"✅ Добавлено в Notion #{task_id}\n"
        f"Текст: {task.title}\n"
        f"Срок: {format_dt(task.due_at)}\n"
        f"Напоминание: {format_dt(task.remind_at)}\n"
        f"Повтор: {task.repeat_rule or '—'}"
    )


async def reminder_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    db_path: str = context.bot_data["db_path"]
    job_data = context.job.data or {}
    task_id = job_data.get("task_id")
    chat_id = job_data.get("chat_id")
    if not task_id or not chat_id:
        return
    task = get_task(db_path, task_id)
    if not task or task.status != "open":
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


async def on_startup(app: Application) -> None:
    settings: Settings = app.bot_data["settings"]
    db_path: str = app.bot_data["db_path"]
    now = datetime.now(settings.tz)
    for task in list_future_reminders(db_path, now):
        schedule_reminder(app, task)


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
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, capture_message)
    )
    application.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
