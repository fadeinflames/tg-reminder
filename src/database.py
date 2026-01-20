from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


@dataclass
class Task:
    id: int | None
    user_id: int
    chat_id: int
    title: str
    description: str | None
    due_at: datetime | None
    remind_at: datetime | None
    repeat_rule: str | None
    status: str
    created_at: datetime
    updated_at: datetime


def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                due_at TEXT,
                remind_at TEXT,
                repeat_rule TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        user_id=row["user_id"],
        chat_id=row["chat_id"],
        title=row["title"],
        description=row["description"],
        due_at=_to_dt(row["due_at"]),
        remind_at=_to_dt(row["remind_at"]),
        repeat_rule=row["repeat_rule"],
        status=row["status"],
        created_at=_to_dt(row["created_at"]) or datetime.utcnow(),
        updated_at=_to_dt(row["updated_at"]) or datetime.utcnow(),
    )


def _to_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _to_str(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def create_task(db_path: str, task: Task) -> int:
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO tasks (
                user_id, chat_id, title, description, due_at, remind_at,
                repeat_rule, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.user_id,
                task.chat_id,
                task.title,
                task.description,
                _to_str(task.due_at),
                _to_str(task.remind_at),
                task.repeat_rule,
                task.status,
                _to_str(task.created_at),
                _to_str(task.updated_at),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def list_tasks(db_path: str, user_id: int, status: str = "open") -> list[Task]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM tasks
            WHERE user_id = ? AND status = ?
            ORDER BY COALESCE(due_at, created_at)
            """,
            (user_id, status),
        ).fetchall()
    return [_row_to_task(row) for row in rows]


def get_task(db_path: str, task_id: int, user_id: int | None = None) -> Task | None:
    with _connect(db_path) as conn:
        if user_id is None:
            row = conn.execute(
                "SELECT * FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM tasks WHERE id = ? AND user_id = ?",
                (task_id, user_id),
            ).fetchone()
    return _row_to_task(row) if row else None


def update_task_status(
    db_path: str, task_id: int, status: str, updated_at: datetime
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE tasks SET status = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, _to_str(updated_at), task_id),
        )
        conn.commit()


def delete_task(db_path: str, task_id: int, user_id: int) -> bool:
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM tasks WHERE id = ? AND user_id = ?",
            (task_id, user_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def update_task_remind_at(db_path: str, task_id: int, remind_at: datetime) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE tasks SET remind_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (_to_str(remind_at), _to_str(datetime.utcnow()), task_id),
        )
        conn.commit()


def list_future_reminders(db_path: str, now: datetime) -> list[Task]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM tasks
            WHERE status = 'open' AND remind_at IS NOT NULL AND remind_at > ?
            """,
            (_to_str(now),),
        ).fetchall()
    return [_row_to_task(row) for row in rows]


def list_due_tasks(db_path: str, now: datetime) -> Iterable[Task]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM tasks
            WHERE status = 'open' AND due_at IS NOT NULL AND due_at <= ?
            """,
            (_to_str(now),),
        ).fetchall()
    return [_row_to_task(row) for row in rows]
