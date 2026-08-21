"""Subjects, levels, and question bank (admin-managed)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from botocore.exceptions import ClientError

from app import db, keys
from app.validation import LevelCreate, STEM_CATEGORIES, SubjectCreate, parse_csv_questions


class SubjectNotFound(Exception):
    pass


class LevelNotFound(Exception):
    pass


class ConflictError(Exception):
    pass


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify_subject_id(category: str, topic: str) -> str:
    """Build a stable subject_id from category + topic (lowercase slug)."""
    raw = f"{category}-{topic}".lower().strip()
    s = re.sub(r"[^a-z0-9_-]+", "-", raw)
    s = re.sub(r"-{2,}", "-", s).strip("-_")
    if not s:
        s = "subject"
    if not re.match(r"^[a-z]", s):
        s = f"s-{s}"
    return s[:64]


def create_subject(data: SubjectCreate) -> dict[str, Any]:
    topic = data.resolved_topic()
    category = data.category
    subject_id = data.subject_id or slugify_subject_id(category, topic)

    pk = keys.subject_pk(subject_id)
    sk = keys.subject_meta_sk()
    existing = db.get_item(pk, sk)
    if existing and not existing.get("deleted_at"):
        raise ConflictError(f"Subject '{subject_id}' already exists")

    now = _utcnow_iso()
    grade_level = (getattr(data, "grade_level", None) or "") or ""
    # name kept as topic for backward-compatible study UI consumers
    item = {
        "PK": pk,
        "SK": sk,
        "GSI1PK": keys.ENTITY_SUBJECT,
        "GSI1SK": f"{data.sort_order:05d}#{subject_id}",
        "entity_type": "SUBJECT",
        "subject_id": subject_id,
        "category": category,
        "topic": topic,
        "name": topic,
        "description": data.description or "",
        "sort_order": data.sort_order,
        "grade_level": grade_level,
        "created_at": now,
        "updated_at": now,
        "deleted_at": "",
    }
    try:
        db.put_item(item, condition="attribute_not_exists(PK)")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ConflictError(f"Subject '{subject_id}' already exists") from exc
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


def update_subject(subject_id: str, data) -> dict[str, Any]:
    """Update topic/description/category/sort_order and cascade tags to levels.

    Questions inherit category/topic from the subject when listed (no per-row
    cascade) so Edit Subject stays fast even with large question banks.
    """
    get_subject(subject_id)
    payload = data.model_dump(exclude_unset=True)
    if not payload:
        return _public_subject(get_subject(subject_id))

    now = _utcnow_iso()
    updates: dict[str, Any] = {"updated_at": now}
    tags_changed = False

    if "category" in payload and payload["category"] is not None:
        updates["category"] = payload["category"]
        tags_changed = True
    if "topic" in payload and payload["topic"] is not None:
        topic = str(payload["topic"]).strip()
        if not topic:
            raise ValueError("topic cannot be empty")
        updates["topic"] = topic
        updates["name"] = topic  # keep legacy name in sync with topic
        tags_changed = True
    if "description" in payload and payload["description"] is not None:
        updates["description"] = payload["description"]
    if "sort_order" in payload and payload["sort_order"] is not None:
        order = int(payload["sort_order"])
        updates["sort_order"] = order
        updates["GSI1SK"] = f"{order:05d}#{subject_id}"
    if "grade_level" in payload and payload["grade_level"] is not None:
        # Empty string clears the tag
        updates["grade_level"] = str(payload["grade_level"] or "").strip()

    db.update_item(
        keys.subject_pk(subject_id),
        keys.subject_meta_sk(),
        updates,
    )
    updated = get_subject(subject_id)

    if tags_changed:
        category = _subject_category(updated)
        topic = _subject_topic(updated)
        try:
            _propagate_subject_tags(subject_id, category, topic, now=now)
        except Exception:
            # Subject META is already saved; level denormalized tags can lag
            # without failing the admin save.
            pass

    return _public_subject(get_subject(subject_id))


def _propagate_subject_tags(
    subject_id: str,
    category: str,
    topic: str,
    *,
    now: str | None = None,
) -> int:
    """Write category/topic onto active LEVEL rows only (not every question).

    Updating hundreds of questions one-by-one was timing out Lambda (15s) on
    Edit Subject. Questions resolve tags from the parent subject at read time.
    """
    stamp = now or _utcnow_iso()
    pk = keys.subject_pk(subject_id)
    items = db.query_pk(pk, sk_begins_with="LEVEL#")
    count = 0
    for item in items:
        if item.get("deleted_at"):
            continue
        # Only real level metadata rows — SK is LEVEL#id (no #Q#)
        if item.get("entity_type") != "LEVEL":
            continue
        sk = item.get("SK") or ""
        if "#Q#" in sk:
            continue
        db.update_item(
            pk,
            sk,
            {"category": category, "topic": topic, "updated_at": stamp},
        )
        count += 1
    return count


def create_level(subject_id: str, data: LevelCreate) -> dict[str, Any]:
    subject = get_subject(subject_id)
    pk = keys.subject_pk(subject_id)
    sk = keys.level_sk(data.level_id)
    existing = db.get_item(pk, sk)
    if existing and not existing.get("deleted_at"):
        raise ConflictError(f"Level '{data.level_id}' already exists")

    now = _utcnow_iso()
    category = _subject_category(subject)
    topic = _subject_topic(subject)
    item = {
        "PK": pk,
        "SK": sk,
        "GSI1PK": keys.ENTITY_LEVEL,
        "GSI1SK": f"{subject_id}#{data.order:05d}#{data.level_id}",
        "entity_type": "LEVEL",
        # Tag level to its working subject (category + topic + subject_id)
        "subject_id": subject_id,
        "category": category,
        "topic": topic,
        "level_id": data.level_id,
        "name": data.name,
        "description": data.description or "",
        "order": data.order,
        "pass_accuracy": data.pass_accuracy,
        "min_questions": data.min_questions,
        "question_count": 0,
        # Monotonic version bumped when questions are imported/updated/deleted
        "content_version": 0,
        "content_updated_at": now,
        "created_at": now,
        "updated_at": now,
        "deleted_at": "",
    }
    db.put_item(item)
    return _public_level(item)


def touch_level_content(subject_id: str, level_id: str, *, now: str | None = None) -> int:
    """Bump content_version when the question bank changes. Returns new version."""
    stamp = now or _utcnow_iso()
    level = get_level(subject_id, level_id)
    try:
        ver = int(level.get("content_version") or 0) + 1
    except (TypeError, ValueError):
        ver = 1
    db.update_item(
        keys.subject_pk(subject_id),
        keys.level_sk(level_id),
        {
            "content_version": ver,
            "content_updated_at": stamp,
            "updated_at": stamp,
        },
    )
    return ver


def get_level(subject_id: str, level_id: str) -> dict[str, Any]:
    item = db.get_item(keys.subject_pk(subject_id), keys.level_sk(level_id))
    if not item or item.get("deleted_at") or item.get("entity_type") != "LEVEL":
        raise LevelNotFound(f"Level '{level_id}' not found in subject '{subject_id}'")
    return item


def list_levels(subject_id: str) -> list[dict[str, Any]]:
    """List active levels for a subject (META only — does not scan questions).

    Levels are stored as ``SK=LEVEL#<id>`` and questions as ``SK=LEVEL#<id>#Q#…``
    under the same PK. Querying the base table with ``begins_with(LEVEL#)``
    therefore reads every question (hundreds–thousands of items per subject).

    Levels also project to GSI1 as ``GSI1PK=ENTITY#LEVEL``,
    ``GSI1SK=<subject_id>#<order>#<level_id>``. Querying that index returns
    only level META rows (O(levels), not O(questions)).
    """
    get_subject(subject_id)
    items = db.query_gsi1(
        keys.ENTITY_LEVEL,
        sk_begins_with=f"{subject_id}#",
    )
    levels = [
        _public_level(i)
        for i in items
        if i.get("entity_type") == "LEVEL"
        and not i.get("deleted_at")
        and (i.get("subject_id") or "") == subject_id
        # Belt-and-suspenders: question SKs contain #Q#
        and "#Q#" not in (i.get("SK") or "")
    ]
    # GSI1SK is subject#order#level_id so results are already order-ish;
    # sort explicitly for stable API (order field is source of truth).
    levels.sort(key=lambda lv: (int(lv.get("order") or 0), str(lv.get("level_id") or "")))
    return levels


class QuestionNotFound(Exception):
    pass


def update_level(subject_id: str, level_id: str, data) -> dict[str, Any]:
    """Partial update of level metadata (admin)."""
    get_level(subject_id, level_id)
    payload = data.model_dump(exclude_unset=True)
    if not payload:
        return _public_level(get_level(subject_id, level_id))
    now = _utcnow_iso()
    updates: dict[str, Any] = {"updated_at": now}
    for field in ("name", "description", "order", "pass_accuracy", "min_questions"):
        if field in payload:
            updates[field] = payload[field]
    if "order" in updates:
        updates["GSI1SK"] = f"{subject_id}#{int(updates['order']):05d}#{level_id}"
    item = db.update_item(
        keys.subject_pk(subject_id),
        keys.level_sk(level_id),
        updates,
    )
    return _public_level(item)


def soft_delete_level(subject_id: str, level_id: str) -> dict[str, Any]:
    """Soft-delete level and all of its questions."""
    get_level(subject_id, level_id)
    cleared = clear_questions(subject_id, level_id)
    now = _utcnow_iso()
    db.update_item(
        keys.subject_pk(subject_id),
        keys.level_sk(level_id),
        {"deleted_at": now, "updated_at": now, "question_count": 0},
    )
    return {
        "subject_id": subject_id,
        "level_id": level_id,
        "deleted": True,
        "questions_cleared": cleared["cleared"],
    }


def clear_questions(subject_id: str, level_id: str) -> dict[str, Any]:
    """Soft-delete every question on a level and reset question_count."""
    get_level(subject_id, level_id)
    pk = keys.subject_pk(subject_id)
    prefix = f"LEVEL#{level_id}#Q#"
    items = db.query_pk(pk, sk_begins_with=prefix)
    now = _utcnow_iso()
    cleared = 0
    for item in items:
        if item.get("entity_type") != "QUESTION" or item.get("deleted_at"):
            continue
        db.update_item(
            pk,
            item["SK"],
            {"deleted_at": now, "updated_at": now},
        )
        cleared += 1
    try:
        ver = int(get_level(subject_id, level_id).get("content_version") or 0) + 1
    except (TypeError, ValueError):
        ver = 1
    db.update_item(
        pk,
        keys.level_sk(level_id),
        {
            "question_count": 0,
            "updated_at": now,
            "content_updated_at": now,
            "content_version": ver,
        },
    )
    return {
        "subject_id": subject_id,
        "level_id": level_id,
        "cleared": cleared,
        "question_count": 0,
    }


def import_questions_csv(
    subject_id: str,
    level_id: str,
    csv_text: str,
    *,
    replace: bool = False,
) -> dict[str, Any]:
    """Import questions from CSV. Append by default; replace=True clears first."""
    level = get_level(subject_id, level_id)
    # Prefer subject meta for tags; fall back to denormalized level fields
    try:
        subject = get_subject(subject_id)
        category = _subject_category(subject)
        topic = _subject_topic(subject)
    except SubjectNotFound:
        category = level.get("category") or "Mathematics"
        topic = level.get("topic") or ""
    parsed = parse_csv_questions(csv_text)
    now = _utcnow_iso()
    pk = keys.subject_pk(subject_id)
    cleared = 0
    if replace:
        cleared = clear_questions(subject_id, level_id)["cleared"]
        base_count = 0
    else:
        base_count = int(level.get("question_count") or 0)

    # Preserve CSV/Excel row order via sort_order (and zero-padded SK prefix).
    # Active questions already use sort_order 0..n-1 when imported after this change.
    next_order = 0 if replace else _max_question_sort_order(subject_id, level_id) + 1

    batch_items: list[dict[str, Any]] = []
    for i, row in enumerate(parsed):
        sort_order = next_order + i
        # Zero-padded prefix keeps DynamoDB SK order aligned with import order;
        # random suffix avoids collisions on re-import/append.
        qid = f"{sort_order:06d}{uuid.uuid4().hex[:6]}"
        batch_items.append(
            {
                "PK": pk,
                "SK": keys.question_sk(level_id, qid),
                "entity_type": "QUESTION",
                # Tag question to working subject (subject_id + category + topic)
                "subject_id": subject_id,
                "category": category,
                "topic": topic,
                "level_id": level_id,
                "question_id": qid,
                "prompt": row["prompt"],
                "answer": row["answer"],
                "sort_order": sort_order,
                "created_at": now,
                "updated_at": now,
                "deleted_at": "",
            }
        )
    created = db.batch_put_items(batch_items)

    new_count = base_count + created
    # After replace, clear_questions already bumped version once; still bump for the import
    try:
        ver = int(get_level(subject_id, level_id).get("content_version") or 0) + 1
    except (TypeError, ValueError):
        ver = 1
    db.update_item(
        pk,
        keys.level_sk(level_id),
        {
            "question_count": new_count,
            "updated_at": now,
            "content_updated_at": now,
            "content_version": ver,
        },
    )
    return {
        "subject_id": subject_id,
        "level_id": level_id,
        "imported": created,
        "cleared": cleared,
        "replaced": replace,
        "question_count": new_count,
    }


def _max_question_sort_order(subject_id: str, level_id: str) -> int:
    """Highest sort_order among active questions (-1 if none)."""
    prefix = f"LEVEL#{level_id}#Q#"
    items = db.query_pk(keys.subject_pk(subject_id), sk_begins_with=prefix)
    max_so = -1
    for i in items:
        if i.get("deleted_at") or i.get("entity_type") != "QUESTION":
            continue
        so = i.get("sort_order")
        if so is None:
            continue
        try:
            max_so = max(max_so, int(so))
        except (TypeError, ValueError):
            continue
    return max_so


def update_question(
    subject_id: str,
    level_id: str,
    question_id: str,
    data,
) -> dict[str, Any]:
    item = get_question(subject_id, level_id, question_id)
    if not item:
        raise QuestionNotFound(f"Question '{question_id}' not found")
    payload = data.model_dump(exclude_unset=True)
    if not payload:
        return {
            "question_id": item.get("question_id"),
            "prompt": item.get("prompt"),
            "answer": item.get("answer"),
            "level_id": level_id,
            "subject_id": subject_id,
        }
    now = _utcnow_iso()
    updates = {"updated_at": now}
    for field in ("prompt", "answer"):
        if field in payload:
            updates[field] = payload[field]
    updated = db.update_item(
        keys.subject_pk(subject_id),
        keys.question_sk(level_id, question_id),
        updates,
    )
    touch_level_content(subject_id, level_id, now=now)
    return {
        "question_id": updated.get("question_id"),
        "prompt": updated.get("prompt"),
        "answer": updated.get("answer"),
        "level_id": level_id,
        "subject_id": subject_id,
    }


def soft_delete_question(subject_id: str, level_id: str, question_id: str) -> None:
    item = get_question(subject_id, level_id, question_id)
    if not item:
        raise QuestionNotFound(f"Question '{question_id}' not found")
    now = _utcnow_iso()
    db.update_item(
        keys.subject_pk(subject_id),
        keys.question_sk(level_id, question_id),
        {"deleted_at": now, "updated_at": now},
    )
    # Recount active questions + bump content version
    remaining = list_questions(subject_id, level_id, include_answers=False)
    try:
        ver = int(get_level(subject_id, level_id).get("content_version") or 0) + 1
    except (TypeError, ValueError):
        ver = 1
    db.update_item(
        keys.subject_pk(subject_id),
        keys.level_sk(level_id),
        {
            "question_count": len(remaining),
            "updated_at": now,
            "content_updated_at": now,
            "content_version": ver,
        },
    )


def list_questions(
    subject_id: str,
    level_id: str,
    *,
    include_answers: bool = False,
) -> list[dict[str, Any]]:
    """Return active questions in import order (CSV/Excel first row first)."""
    get_level(subject_id, level_id)
    prefix = f"LEVEL#{level_id}#Q#"
    # Ascending SK; new IDs are zero-padded by sort_order so order matches import.
    items = db.query_pk(
        keys.subject_pk(subject_id),
        sk_begins_with=prefix,
        scan_forward=True,
    )
    active: list[dict[str, Any]] = []
    for i in items:
        if i.get("deleted_at") or i.get("entity_type") != "QUESTION":
            continue
        active.append(i)

    # Explicit sort: prefer sort_order (CSV/Excel sequence), then SK as tiebreaker.
    def _order_key(item: dict[str, Any]) -> tuple:
        so = item.get("sort_order")
        try:
            so_i = int(so) if so is not None else 10**9
        except (TypeError, ValueError):
            so_i = 10**9
        return (so_i, str(item.get("SK") or item.get("question_id") or ""))

    active.sort(key=_order_key)

    # Subject is source of truth for category/topic (Edit Subject updates META only;
    # denormalized fields on questions may lag and must not win over subject).
    try:
        subject = get_subject(subject_id)
        category = _subject_category(subject)
        topic = _subject_topic(subject)
    except SubjectNotFound:
        category = "Mathematics"
        topic = ""

    result = []
    for i in active:
        q = {
            "question_id": i.get("question_id"),
            "prompt": i.get("prompt"),
            "level_id": level_id,
            "subject_id": subject_id,
            "category": category,
            "topic": topic,
            "subject_label": f"{category} - {topic}" if category and topic else None,
            "sort_order": i.get("sort_order"),
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
                category="Mathematics",
                topic="Arithmetic",
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


def _subject_category(item: dict[str, Any]) -> str:
    cat = (item.get("category") or "").strip()
    if cat:
        for c in STEM_CATEGORIES:
            if cat.lower() == c.lower():
                return c
    # Legacy rows: seed used subject_id "math" / name "Mathematics"
    name = (item.get("name") or "").strip()
    sid = (item.get("subject_id") or "").strip().lower()
    if sid == "math" or name.lower() == "mathematics":
        return "Mathematics"
    for c in STEM_CATEGORIES:
        if sid.startswith(c.lower()) or name.lower().startswith(c.lower()):
            return c
    return "Mathematics"


def _subject_topic(item: dict[str, Any]) -> str:
    topic = (item.get("topic") or "").strip()
    if topic:
        return topic
    name = (item.get("name") or "").strip()
    category = _subject_category(item)
    # Avoid "Mathematics - Mathematics" for legacy seed name
    if name and name.lower() != category.lower():
        return name
    if (item.get("subject_id") or "").lower() == "math":
        return "Arithmetic"
    return name or (item.get("subject_id") or "Topic")


def subject_label(item: dict[str, Any]) -> str:
    """Display format: <Category> - <Topic>."""
    return f"{_subject_category(item)} - {_subject_topic(item)}"


def _public_subject(item: dict[str, Any]) -> dict[str, Any]:
    category = _subject_category(item)
    topic = _subject_topic(item)
    grade = (item.get("grade_level") or "").strip() or None
    return {
        "subject_id": item.get("subject_id"),
        "category": category,
        "topic": topic,
        "name": topic,  # study UI title uses topic; full label is separate
        "label": f"{category} - {topic}",
        "description": item.get("description", ""),
        "sort_order": item.get("sort_order", 0),
        "grade_level": grade,
        "created_at": item.get("created_at"),
    }


def _public_level(item: dict[str, Any]) -> dict[str, Any]:
    category = item.get("category") or None
    topic = item.get("topic") or None
    # Backfill from subject when older levels lack denormalized tags
    if not category or not topic:
        sid = item.get("subject_id")
        if sid:
            try:
                subj = get_subject(sid)
                category = category or _subject_category(subj)
                topic = topic or _subject_topic(subj)
            except SubjectNotFound:
                category = category or "Mathematics"
                topic = topic or ""
    return {
        "subject_id": item.get("subject_id"),
        "category": category,
        "topic": topic,
        "subject_label": f"{category} - {topic}" if category and topic else None,
        "level_id": item.get("level_id"),
        "name": item.get("name"),
        "description": item.get("description", ""),
        "order": item.get("order"),
        "pass_accuracy": float(item.get("pass_accuracy", 0.8)),
        "min_questions": int(item.get("min_questions", 5)),
        "question_count": int(item.get("question_count") or 0),
        "content_version": int(item.get("content_version") or 0),
        "content_updated_at": item.get("content_updated_at") or item.get("updated_at"),
        "created_at": item.get("created_at"),
    }
