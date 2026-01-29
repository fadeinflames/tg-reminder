from __future__ import annotations

import logging
from datetime import datetime, time

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .config import Settings, load_settings
from .database import Task, create_task, delete_task, get_task, init_db, list_future_reminders
from .database import (
    list_chat_ids_with_open_tasks,
    list_tasks_for_chat,
    update_task_fields,
    update_task_status,
)
from .parser import parse_task_text
from .utils import format_dt, next_due_date


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
        "Просто отправь сообщение, и оно станет задачей.\n"
        "Пример: Созвон с клиентом завтра 15:00 напомни за 1 час\n"
        "Повторы: ежедневно, еженедельно, каждые 3 дня\n"
        "Команды: /list, /done <id>, /delete <id>, /sync"
    )


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        await _deny(update)
        return
    db_path: str = context.bot_data["db_path"]
    chat_id = update.effective_chat.id if update.effective_chat else None
    if not chat_id:
        return
    tasks = list_tasks_for_chat(db_path, chat_id, status="open")
    if not tasks:
        await update.message.reply_text("Открытых задач нет.")
        return
    lines = [f"📋 Открытые задачи ({len(tasks)}):"]
    lines.extend(_format_task_lines(tasks))
    keyboard = _build_done_keyboard(tasks)
    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
    )


async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        await _deny(update)
        return
    rescheduled = reschedule_all_reminders(context)
    await update.message.reply_text(
        f"✅ Напоминания пересчитаны: {rescheduled}"
    )


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

    await update.message.reply_text(
        f"✅ Добавлено #{task_id}\n"
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


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        await _deny(update)
        return
    db_path: str = context.bot_data["db_path"]
    settings: Settings = context.bot_data["settings"]
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Используй: /done <id>")
        return
    task_id = int(context.args[0])
    task = get_task(db_path, task_id, update.effective_user.id)
    if not task:
        await update.message.reply_text("Задача не найдена.")
        return
    message = _complete_task(task, context, settings, db_path)
    await update.message.reply_text(message)


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update, context):
        await _deny(update)
        return
    db_path: str = context.bot_data["db_path"]
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Используй: /delete <id>")
        return
    task_id = int(context.args[0])
    task = get_task(db_path, task_id, update.effective_user.id)
    if not task:
        await update.message.reply_text("Задача не найдена.")
        return
    if delete_task(db_path, task_id, update.effective_user.id):
        remove_reminder(context.application, task_id)
        await update.message.reply_text("🗑️ Задача удалена.")
        return
    await update.message.reply_text("Не удалось удалить задачу.")


async def done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    logger.info("Callback received: %s", query.data)
    if not _is_allowed(update, context):
        await query.answer("Нет доступа", show_alert=True)
        return
    await query.answer("Ок")
    if not query.data.startswith("done:"):
        return
    task_id_text = query.data.split(":", 1)[1]
    if not task_id_text.isdigit():
        await query.answer("Некорректный id", show_alert=True)
        return
    task_id = int(task_id_text)
    db_path: str = context.bot_data["db_path"]
    settings: Settings = context.bot_data["settings"]
    user_id = update.effective_user.id if update.effective_user else None
    task = get_task(db_path, task_id, user_id)
    if not task:
        await query.answer("Задача не найдена", show_alert=True)
        return
    message = _complete_task(task, context, settings, db_path)
    try:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(message)
    except TelegramError:
        logger.exception("Failed to edit message after done callback")
        if query.message:
            await query.message.reply_text(message)


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


def reschedule_all_reminders(context: ContextTypes.DEFAULT_TYPE) -> int:
    db_path: str = context.bot_data["db_path"]
    settings: Settings = context.bot_data["settings"]
    _remove_all_reminders(context.application)
    now = datetime.now(settings.tz)
    count = 0
    for task in list_future_reminders(db_path, now):
        schedule_reminder(context.application, task)
        count += 1
    return count


def _remove_all_reminders(app: Application) -> None:
    for job in list(app.job_queue.jobs()):
        if job.name and job.name.startswith("remind_"):
            job.schedule_removal()




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


def _format_task_lines(tasks: list[Task]) -> list[str]:
    lines = []
    for task in tasks:
        title = " ".join(task.title.split())
        lines.append(
            f"• {title} | срок: {format_dt(task.due_at)} | "
            f"напомнить: {format_dt(task.remind_at)}"
        )
    return lines


def _build_done_keyboard(tasks: list[Task]) -> list[list[InlineKeyboardButton]]:
    keyboard: list[list[InlineKeyboardButton]] = []
    for task in tasks:
        title = " ".join(task.title.split())
        label = title if len(title) <= 40 else f"{title[:37]}..."
        keyboard.append([InlineKeyboardButton(f"✅ {label}", callback_data=f"done:{task.id}")])
    return keyboard


def _complete_task(
    task: Task,
    context: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
    db_path: str,
) -> str:
    now = datetime.now(settings.tz)
    next_due = next_due_date(task.due_at, task.repeat_rule)
    if task.repeat_rule and next_due:
        remove_reminder(context.application, task.id)
        new_remind = None
        if task.remind_at and task.due_at and task.remind_at < task.due_at:
            offset = task.due_at - task.remind_at
            candidate = next_due - offset
            if candidate > now:
                new_remind = candidate
        update_task_fields(
            db_path,
            task.id,
            due_at=next_due,
            remind_at=new_remind,
            repeat_rule=task.repeat_rule,
        )
        update_task_status(db_path, task.id, "open", datetime.utcnow())
        if new_remind:
            task.due_at = next_due
            task.remind_at = new_remind
            schedule_reminder(context.application, task)
        return f"✅ Повтор перенесён на {format_dt(next_due)}"
    update_task_status(db_path, task.id, "done", datetime.utcnow())
    remove_reminder(context.application, task.id)
    return "✅ Задача отмечена выполненной."


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
    application.add_handler(CommandHandler("done", done_command))
    application.add_handler(CommandHandler("delete", delete_command))
    application.add_handler(CommandHandler("sync", sync_command))
    application.add_handler(CallbackQueryHandler(done_callback))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, capture_message)
    )
    application.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
