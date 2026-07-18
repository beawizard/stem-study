"""Task CRUD – owner-only with soft delete."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app import db, keys
from app.validation import TaskCreate, TaskUpdate


class TaskNotFound(Exception):
    pass


class TaskForbidden(Exception):
    pass


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return uuid.uuid4().hex


def create_task(user_id: str, data: TaskCreate) -> dict[str, Any]:
    task_id = _new_id()
    now = _utcnow_iso()
    item = {
        "PK": keys.user_pk(user_id),
        "SK": keys.task_sk(task_id),
        "GSI1PK": keys.ENTITY_TASK,
        "GSI1SK": f"{now}#{task_id}",
        "entity_type": "TASK",
        "task_id": task_id,
        "user_id": user_id,
        "title": data.title,
        "description": data.description or "",
        "due_date": data.due_date,
        "subject_id": data.subject_id,
        "completed": False,
        "created_at": now,
        "updated_at": now,
        "deleted_at": "",
    }
    db.put_item(item)
    return _public(item)


def get_task(user_id: str, task_id: str) -> dict[str, Any]:
    item = db.get_item(keys.user_pk(user_id), keys.task_sk(task_id))
    if not item or item.get("deleted_at"):
        raise TaskNotFound(f"Task {task_id} not found")
    if item.get("user_id") != user_id:
        raise TaskForbidden("Not task owner")
    return _public(item)


def list_tasks(user_id: str, *, include_completed: bool = True) -> list[dict[str, Any]]:
    items = db.query_pk(keys.user_pk(user_id), sk_begins_with="TASK#")
    result = []
    for item in items:
        if item.get("deleted_at"):
            continue
        if item.get("entity_type") and item.get("entity_type") != "TASK":
            continue
        if not include_completed and item.get("completed"):
            continue
        result.append(_public(item))
    result.sort(key=lambda t: t.get("created_at") or "", reverse=True)
    return result


def update_task(user_id: str, task_id: str, data: TaskUpdate) -> dict[str, Any]:
    get_task(user_id, task_id)
    updates: dict[str, Any] = {"updated_at": _utcnow_iso()}
    payload = data.model_dump(exclude_unset=True)
    for field in ("title", "description", "due_date", "completed", "subject_id"):
        if field in payload:
            updates[field] = payload[field]
    item = db.update_item(keys.user_pk(user_id), keys.task_sk(task_id), updates)
    return _public(item)


def soft_delete_task(user_id: str, task_id: str) -> None:
    get_task(user_id, task_id)
    db.update_item(
        keys.user_pk(user_id),
        keys.task_sk(task_id),
        {"deleted_at": _utcnow_iso(), "updated_at": _utcnow_iso()},
    )


def _public(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": item.get("task_id"),
        "title": item.get("title"),
        "description": item.get("description", ""),
        "due_date": item.get("due_date"),
        "subject_id": item.get("subject_id"),
        "completed": bool(item.get("completed")),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }
