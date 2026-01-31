from datetime import datetime

from src.bot import _cleanup_tasks
from src.database import Task, create_task, init_db, list_tasks


def _make_task(user_id: int, chat_id: int, title: str) -> Task:
    now = datetime.utcnow()
    return Task(
        id=None,
        user_id=user_id,
        chat_id=chat_id,
        title=title,
        description=None,
        due_at=None,
        remind_at=None,
        repeat_rule=None,
        notion_page_id=None,
        status="open",
        created_at=now,
        updated_at=now,
    )


def test_cleanup_only_affects_user(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    user_id = 354573537
    other_user_id = 111
    chat_id = 100

    create_task(db_path, _make_task(user_id, chat_id, "task a"))
    create_task(db_path, _make_task(user_id, chat_id, "task b"))
    create_task(db_path, _make_task(other_user_id, chat_id, "task a"))

    removed_ids = _cleanup_tasks(db_path, user_id, ["task a"])
    assert len(removed_ids) == 1

    remaining_user = list_tasks(db_path, user_id, status="open")
    remaining_other = list_tasks(db_path, other_user_id, status="open")

    assert [task.title for task in remaining_user] == ["task a"]
    assert [task.title for task in remaining_other] == ["task a"]
