"""Subjects, levels, and question bank (admin-managed)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from botocore.exceptions import ClientError

from app import db, keys
from app.validation import LevelCreate, SubjectCreate, parse_csv_questions


class SubjectNotFound(Exception):
    pass


class LevelNotFound(Exception):
    pass


class ConflictError(Exception):
    pass


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_subject(data: SubjectCreate) -> dict[str, Any]:
    pk = keys.subject_pk(data.subject_id)
    sk = keys.subject_meta_sk()
    existing = db.get_item(pk, sk)
    if existing and not existing.get("deleted_at"):
        raise ConflictError(f"Subject '{data.subject_id}' already exists")

    now = _utcnow_iso()
    item = {
        "PK": pk,
        "SK": sk,
        "GSI1PK": keys.ENTITY_SUBJECT,
        "GSI1SK": f"{data.sort_order:05d}#{data.subject_id}",
        "entity_type": "SUBJECT",
        "subject_id": data.subject_id,
        "name": data.name,
        "description": data.description or "",
        "sort_order": data.sort_order,
        "created_at": now,
        "updated_at": now,
        "deleted_at": "",
    }
    try:
        db.put_item(item, condition="attribute_not_exists(PK)")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ConflictError(f"Subject '{data.subject_id}' already exists") from exc
        raise
    return _public_subject(item)


def get_subject(subject_id: str) -> dict[str, Any]:
    item = db.get_item(keys.subject_pk(subject_id), keys.subject_meta_sk())
    if not item or item.get("deleted_at"):
        raise SubjectNotFound(f"Subject '{subject_id}' not found")
    return item


def list_subjects() -> list[dict[str, Any]]:
    items = db.query_gsi1(keys.ENTITY_SUBJECT)
    result = [
        _public_subject(i)
        for i in items
        if i.get("entity_type") == "SUBJECT" and not i.get("deleted_at")
    ]
    result.sort(key=lambda s: (s.get("sort_order", 0), s.get("subject_id", "")))
    return result


def soft_delete_subject(subject_id: str) -> None:
    get_subject(subject_id)
    db.update_item(
        keys.subject_pk(subject_id),
        keys.subject_meta_sk(),
        {"deleted_at": _utcnow_iso(), "updated_at": _utcnow_iso()},
    )


def create_level(subject_id: str, data: LevelCreate) -> dict[str, Any]:
    get_subject(subject_id)
    pk = keys.subject_pk(subject_id)
    sk = keys.level_sk(data.level_id)
    existing = db.get_item(pk, sk)
    if existing and not existing.get("deleted_at"):
        raise ConflictError(f"Level '{data.level_id}' already exists")

    now = _utcnow_iso()
    item = {
        "PK": pk,
        "SK": sk,
        "GSI1PK": keys.ENTITY_LEVEL,
        "GSI1SK": f"{subject_id}#{data.order:05d}#{data.level_id}",
        "entity_type": "LEVEL",
        "subject_id": subject_id,
        "level_id": data.level_id,
        "name": data.name,
        "description": data.description or "",
        "order": data.order,
        "pass_accuracy": data.pass_accuracy,
        "min_questions": data.min_questions,
        "question_count": 0,
        "created_at": now,
        "updated_at": now,
        "deleted_at": "",
    }
    db.put_item(item)
    return _public_level(item)


def get_level(subject_id: str, level_id: str) -> dict[str, Any]:
    item = db.get_item(keys.subject_pk(subject_id), keys.level_sk(level_id))
    if not item or item.get("deleted_at") or item.get("entity_type") != "LEVEL":
        raise LevelNotFound(f"Level '{level_id}' not found in subject '{subject_id}'")
    return item


def list_levels(subject_id: str) -> list[dict[str, Any]]:
    get_subject(subject_id)
    items = db.query_pk(keys.subject_pk(subject_id), sk_begins_with="LEVEL#")
    levels = [
        _public_level(i)
        for i in items
        if i.get("entity_type") == "LEVEL" and not i.get("deleted_at")
        # exclude questions: SK is LEVEL#x#Q#y
        and "#Q#" not in i.get("SK", "")
    ]
    levels.sort(key=lambda lv: lv.get("order", 0))
    return levels


def import_questions_csv(subject_id: str, level_id: str, csv_text: str) -> dict[str, Any]:
    """Replace/append questions from CSV for a level. Returns import summary."""
    level = get_level(subject_id, level_id)
    parsed = parse_csv_questions(csv_text)
    now = _utcnow_iso()
    pk = keys.subject_pk(subject_id)
    created = 0

    for row in parsed:
        qid = uuid.uuid4().hex[:12]
        item = {
            "PK": pk,
            "SK": keys.question_sk(level_id, qid),
            "entity_type": "QUESTION",
            "subject_id": subject_id,
            "level_id": level_id,
            "question_id": qid,
            "prompt": row["prompt"],
            "answer": row["answer"],
            "created_at": now,
            "updated_at": now,
            "deleted_at": "",
        }
        db.put_item(item)
        created += 1

    new_count = int(level.get("question_count") or 0) + created
    db.update_item(
        pk,
        keys.level_sk(level_id),
        {"question_count": new_count, "updated_at": now},
    )
    return {
        "subject_id": subject_id,
        "level_id": level_id,
        "imported": created,
        "question_count": new_count,
    }


def list_questions(
    subject_id: str,
    level_id: str,
    *,
    include_answers: bool = False,
) -> list[dict[str, Any]]:
    get_level(subject_id, level_id)
    prefix = f"LEVEL#{level_id}#Q#"
    items = db.query_pk(keys.subject_pk(subject_id), sk_begins_with=prefix)
    result = []
    for i in items:
        if i.get("deleted_at") or i.get("entity_type") != "QUESTION":
            continue
        q = {
            "question_id": i.get("question_id"),
            "prompt": i.get("prompt"),
            "level_id": level_id,
            "subject_id": subject_id,
        }
        if include_answers:
            q["answer"] = i.get("answer")
        result.append(q)
    return result


def get_question(subject_id: str, level_id: str, question_id: str) -> dict[str, Any] | None:
    item = db.get_item(
        keys.subject_pk(subject_id),
        keys.question_sk(level_id, question_id),
    )
    if not item or item.get("deleted_at"):
        return None
    return item


def seed_math_defaults() -> dict[str, Any]:
    """Idempotent seed: Math subject + beginner levels with sample questions."""
    from app.validation import LevelCreate, SubjectCreate

    try:
        create_subject(
            SubjectCreate(
                subject_id="math",
                name="Mathematics",
                description="Arithmetic and number skills",
                sort_order=1,
            )
        )
        subject_created = True
    except ConflictError:
        subject_created = False

    levels_spec = [
        ("l1", "Level 1 – Addition", 1, "1,+,1,=,2\n2,+,3,=,5\n4,+,5,=,9\n0,+,7,=,7\n6,+,2,=,8\n"),
        ("l2", "Level 2 – Subtraction", 2, "5,-,2,=,3\n9,-,4,=,5\n7,-,3,=,4\n10,-,6,=,4\n8,-,1,=,7\n"),
        ("l3", "Level 3 – Mixed", 3, "3,+,4,=,7\n10,-,3,=,7\n6,+,6,=,12\n15,-,5,=,10\n2,+,9,=,11\n"),
    ]
    levels_created = 0
    questions_imported = 0
    for lid, name, order, csv_body in levels_spec:
        try:
            create_level(
                "math",
                LevelCreate(
                    level_id=lid,
                    name=name,
                    order=order,
                    pass_accuracy=0.8,
                    min_questions=5,
                ),
            )
            levels_created += 1
            summary = import_questions_csv("math", lid, csv_body)
            questions_imported += summary["imported"]
        except ConflictError:
            # Level exists; only import if empty
            level = get_level("math", lid)
            if int(level.get("question_count") or 0) == 0:
                summary = import_questions_csv("math", lid, csv_body)
                questions_imported += summary["imported"]

    return {
        "subject_created": subject_created,
        "levels_created": levels_created,
        "questions_imported": questions_imported,
    }


def _public_subject(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "subject_id": item.get("subject_id"),
        "name": item.get("name"),
        "description": item.get("description", ""),
        "sort_order": item.get("sort_order", 0),
        "created_at": item.get("created_at"),
    }


def _public_level(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "subject_id": item.get("subject_id"),
        "level_id": item.get("level_id"),
        "name": item.get("name"),
        "description": item.get("description", ""),
        "order": item.get("order"),
        "pass_accuracy": float(item.get("pass_accuracy", 0.8)),
        "min_questions": int(item.get("min_questions", 5)),
        "question_count": int(item.get("question_count") or 0),
        "created_at": item.get("created_at"),
    }
