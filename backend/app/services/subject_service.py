"""Subjects, levels, and question bank (admin-managed)."""

from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Any

from botocore.exceptions import ClientError

from app import db, keys
from app.vedic import (
    TIME_LIMIT_SEC as VEDIC_TIME_LIMIT_SEC,
    detect_exam_kind,
    extract_docx_paragraphs,
    ismc_time_limit_sec,
    looks_like_ismc,
    looks_like_vedic,
    parse_ismc_mcq,
    parse_vedic_mcq,
)
from app.playlists import (
    PLAYLIST_FIELD_NAMES,
    blank_playlist_fields,
    playlist_fields_for_model,
    playlist_update_from_payload,
    public_playlist_fields,
)
from app.validation import (
    MAX_SUBJECT_ID_LEN,
    LevelCreate,
    STEM_CATEGORIES,
    SubjectCreate,
    ensure_subject_id,
    parse_csv_questions,
)


class SubjectNotFound(Exception):
    pass


class LevelNotFound(Exception):
    pass


class ConflictError(Exception):
    pass


class ReplaceNameConflict(Exception):
    """Replace would overwrite a topic stored under a different name."""


# 55-char stem + hyphen + 7 hex chars = the 63-char id limit.
_ID_HASH_LEN = 7
_ID_PREFIX_MAX = MAX_SUBJECT_ID_LEN - _ID_HASH_LEN - 1


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fold_marks(text: str) -> str:
    """NFKD, then drop combining marks so É and ñ become e and n."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_topic_name(text: str) -> str:
    """Identity of a topic name: NFKD, drop combining marks, casefold, trim, collapse spaces.

    frontend/js/app.js normalizeTopicName must stay in sync, including ß → ss,
    final sigma ς → σ, a BOM anywhere in the name, and U+001C–U+001F / U+0085
    as spaces. Cherokee case pairs are not special-cased in the browser.
    """
    raw = str(text or "").replace("\ufeff", "")
    folded = _fold_marks(raw)
    folded = re.sub(r"[\u001c-\u001f\u0085]", " ", folded)
    return re.sub(r"\s+", " ", folded).strip().casefold()


def is_lesson_topic(item: dict[str, Any]) -> bool:
    """Lesson rows the science upload form edits. Technology presentations are not these."""
    kind = (item.get("content_kind") or "").strip()
    if kind in ("science", "lesson"):
        return True
    return bool(item.get("science_ready"))


def _slug_piece(text: str) -> str:
    folded = normalize_topic_name(text)
    slug = re.sub(r"[^a-z0-9_-]+", "-", folded)
    return re.sub(r"-{2,}", "-", slug).strip("-_")


def _hashed_subject_id(prefix: str, normalized: str) -> str:
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:_ID_HASH_LEN]
    stem = (prefix or "").strip("-_")[:_ID_PREFIX_MAX].strip("-_")
    if not stem or not re.match(r"^[a-z]", stem):
        stem = "subject"
    subject_id = ensure_subject_id(f"{stem}-{digest}")
    if not subject_id:
        raise ValueError(f"Topic id must be at most {MAX_SUBJECT_ID_LEN} characters")
    return subject_id


def slugify_subject_id(category: str, topic: str) -> str:
    """Build a subject_id from category + topic, within MAX_SUBJECT_ID_LEN.

    A slug that fits is used as-is. Truncation and empty fallbacks append a
    7-char hash of the full normalized name so different names do not collide.
    """
    normalized = normalize_topic_name(f"{category}-{topic}")
    category_slug = _slug_piece(category)
    if not category_slug or not re.match(r"^[a-z]", category_slug):
        category_slug = "subject"
    topic_slug = _slug_piece(topic)
    if not topic_slug:
        return _hashed_subject_id(category_slug, normalized)
    full = f"{category_slug}-{topic_slug}"
    if len(full) <= MAX_SUBJECT_ID_LEN and re.match(r"^[a-z]", full):
        subject_id = ensure_subject_id(full)
        return subject_id or _hashed_subject_id(category_slug, normalized)
    return _hashed_subject_id(full, normalized)


def reject_replace_if_name_differs(existing: dict[str, Any] | None, submitted_name: str) -> None:
    """Refuse replace when this id is already stored under a different name."""
    if not existing or existing.get("deleted_at"):
        return
    stored = str(existing.get("topic") or existing.get("name") or "").strip()
    submitted = (submitted_name or "").strip()
    if normalize_topic_name(stored) == normalize_topic_name(submitted):
        return
    raise ReplaceNameConflict(
        f"A different topic already uses this id ('{stored}', not '{submitted}'). "
        "Rename this topic, or use Edit Subject to change the existing one."
    )


def display_topic_name(existing: dict[str, Any] | None, submitted_name: str) -> str:
    """Keep the stored spelling when the names match after normalization."""
    submitted = (submitted_name or "").strip()
    if not existing or existing.get("deleted_at"):
        return submitted
    stored = str(existing.get("topic") or existing.get("name") or "").strip()
    if stored and normalize_topic_name(stored) == normalize_topic_name(submitted):
        return stored
    return submitted


def _quiz_name_message(item: dict[str, Any], *, by_id: bool = False) -> str:
    if by_id:
        sid = str(item.get("subject_id") or "").strip() or "this id"
        return f"A quiz subject already uses this topic id ('{sid}'). Rename this topic."
    stored = str(item.get("topic") or item.get("name") or "this name").strip()
    return f"A quiz subject already uses this name ('{stored}'). Rename this topic."


def find_live_topics(category: str, topic: str) -> list[dict[str, Any]]:
    """Live subjects with this category and normalized name, lowest subject_id first."""
    wanted_name = normalize_topic_name(topic)
    wanted_cat = normalize_topic_name(category)
    if not wanted_name or not wanted_cat:
        return []
    matches: list[dict[str, Any]] = []
    for item in db.query_gsi1(keys.ENTITY_SUBJECT):
        if item.get("deleted_at") or item.get("entity_type") != "SUBJECT":
            continue
        if normalize_topic_name(str(item.get("category") or "")) != wanted_cat:
            continue
        stored = str(item.get("topic") or item.get("name") or "")
        if normalize_topic_name(stored) == wanted_name:
            matches.append(item)
    matches.sort(key=lambda item: str(item.get("subject_id") or ""))
    return matches


def find_live_topic(category: str, topic: str) -> dict[str, Any] | None:
    """Live subject with this category and normalized topic name, if any.

    When several match, the lowest subject_id wins. Deleted rows are skipped.
    """
    matches = find_live_topics(category, topic)
    return matches[0] if matches else None


def resolve_topic_upload(
    category: str,
    topic: str,
    *,
    lessons_only: bool = False,
) -> tuple[str, dict[str, Any] | None]:
    """Reuse a live topic's stored id when the normalized name already exists.

    Science, Engineering, and Mathematics uploads only reuse lesson rows, matching
    the admin form. Technology is exempt. A quiz with that name or id is rejected
    instead of being turned into a lesson.
    """
    matches = find_live_topics(category, topic)
    if lessons_only:
        lessons = [item for item in matches if is_lesson_topic(item)]
        if lessons:
            found = lessons[0]
            return str(found["subject_id"]), found
        if matches:
            raise ConflictError(_quiz_name_message(matches[0]))
    elif matches:
        found = matches[0]
        return str(found["subject_id"]), found

    subject_id = slugify_subject_id(category, topic)
    existing = db.get_item(keys.subject_pk(subject_id), keys.subject_meta_sk())
    if (
        lessons_only
        and existing
        and not existing.get("deleted_at")
        and not is_lesson_topic(existing)
    ):
        raise ConflictError(_quiz_name_message(existing, by_id=True))
    return subject_id, existing


def create_subject(data: SubjectCreate) -> dict[str, Any]:
    topic = data.resolved_topic()
    category = data.category
    found = find_live_topic(category, topic)
    if found:
        raise ConflictError(f"Subject '{found.get('subject_id')}' already exists")
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
        **playlist_fields_for_model(None, data),
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
    existing = get_subject(subject_id)
    payload = data.model_dump(exclude_unset=True)
    if not payload:
        return _public_subject(existing)

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
    else:
        topic = str(existing.get("topic") or existing.get("name") or "").strip()
    final_category = str(updates.get("category") or existing.get("category") or "")
    stored_topic = str(existing.get("topic") or existing.get("name") or "")
    stored_category = str(existing.get("category") or "")
    name_same = normalize_topic_name(topic) == normalize_topic_name(stored_topic)
    category_same = normalize_topic_name(final_category) == normalize_topic_name(
        stored_category
    )
    topic_sent = "topic" in payload and payload["topic"] is not None
    category_sent = "category" in payload and payload["category"] is not None
    if (topic_sent or category_sent) and not (name_same and category_same):
        others = [
            item
            for item in find_live_topics(final_category, topic)
            if str(item.get("subject_id") or "") != str(subject_id)
        ]
        if others:
            raise ConflictError(f"Subject '{others[0].get('subject_id')}' already exists")
    if "description" in payload and payload["description"] is not None:
        updates["description"] = payload["description"]
    if "sort_order" in payload and payload["sort_order"] is not None:
        order = int(payload["sort_order"])
        updates["sort_order"] = order
        updates["GSI1SK"] = f"{order:05d}#{subject_id}"
    if "grade_level" in payload and payload["grade_level"] is not None:
        # Empty string clears the tag
        updates["grade_level"] = str(payload["grade_level"] or "").strip()
    category_now = updates.get("category") or existing.get("category") or ""
    if str(category_now) == "Mathematics":
        touched = [
            name
            for name in PLAYLIST_FIELD_NAMES
            if name in payload and payload[name] is not None
        ]
        if touched:
            raise ValueError("Mathematics topics do not use playlists")
        updates.update(blank_playlist_fields())
    else:
        playlist_updates = playlist_update_from_payload(existing, payload)
        if playlist_updates:
            updates.update(playlist_updates)

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
        "exam_kind": "",
        "time_limit_sec": 0,
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
    """Import questions from CSV (or Vedic MCQ text). Append by default."""
    return import_questions(
        subject_id, level_id, text=csv_text, replace=replace
    )


def _contest_paragraphs(text: str) -> list[str]:
    return [ln.strip() for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()]


def import_questions(
    subject_id: str,
    level_id: str,
    *,
    text: str | None = None,
    docx_bytes: bytes | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    """Import arithmetic CSV, a Vedic paper, or an ISMC paper (.docx or text)."""
    level = get_level(subject_id, level_id)
    try:
        subject = get_subject(subject_id)
        category = _subject_category(subject)
        topic = _subject_topic(subject)
    except SubjectNotFound:
        category = level.get("category") or "Mathematics"
        topic = level.get("topic") or ""

    exam_kind = ""
    ismc_limit = 0
    parsed: list[dict[str, Any]]
    paragraphs: list[str] | None = None
    if docx_bytes:
        paragraphs = extract_docx_paragraphs(docx_bytes)
    elif text and (looks_like_ismc(text) or looks_like_vedic(text)):
        paragraphs = _contest_paragraphs(text)

    if paragraphs is not None:
        exam_kind = detect_exam_kind(paragraphs)
        if exam_kind == "ismc":
            parsed = parse_ismc_mcq(paragraphs)
            ismc_limit = ismc_time_limit_sec(paragraphs)
        else:
            parsed = parse_vedic_mcq(paragraphs)
            exam_kind = "vedic"
    else:
        parsed = parse_csv_questions(text or "")

    now = _utcnow_iso()
    pk = keys.subject_pk(subject_id)
    cleared = 0
    if replace:
        cleared = clear_questions(subject_id, level_id)["cleared"]
        base_count = 0
    else:
        base_count = int(level.get("question_count") or 0)

    next_order = 0 if replace else _max_question_sort_order(subject_id, level_id) + 1

    batch_items: list[dict[str, Any]] = []
    for i, row in enumerate(parsed):
        sort_order = next_order + i
        qid = f"{sort_order:06d}{uuid.uuid4().hex[:6]}"
        item: dict[str, Any] = {
            "PK": pk,
            "SK": keys.question_sk(level_id, qid),
            "entity_type": "QUESTION",
            "subject_id": subject_id,
            "category": category,
            "topic": topic,
            "level_id": level_id,
            "question_id": qid,
            "prompt": row["prompt"],
            "answer": row["answer"],
            "sort_order": sort_order,
            "qtype": row.get("qtype") or "open",
            "points": int(row.get("points") or 1),
            "penalty": int(row.get("penalty") or 0),
            "item_no": int(row.get("item_no") or (sort_order + 1)),
            "created_at": now,
            "updated_at": now,
            "deleted_at": "",
        }
        if row.get("choices"):
            item["choices"] = row["choices"]
        batch_items.append(item)
    created = db.batch_put_items(batch_items)

    new_count = base_count + created
    try:
        ver = int(get_level(subject_id, level_id).get("content_version") or 0) + 1
    except (TypeError, ValueError):
        ver = 1
    level_updates: dict[str, Any] = {
        "question_count": new_count,
        "updated_at": now,
        "content_updated_at": now,
        "content_version": ver,
    }
    if exam_kind == "vedic":
        level_updates["exam_kind"] = "vedic"
        level_updates["time_limit_sec"] = VEDIC_TIME_LIMIT_SEC
        level_updates["min_questions"] = max(
            int(level.get("min_questions") or 1), min(created or 1, 40)
        )
        # Handbook: ~60/100 is medal-range on Primary; keep as pass bar.
        if float(level.get("pass_accuracy") or 0) >= 0.8:
            level_updates["pass_accuracy"] = 0.6
    elif exam_kind == "ismc":
        # Flat scoring: do not copy Vedic mark bands, penalties, or the 60/100 bar.
        level_updates["exam_kind"] = "ismc"
        level_updates["time_limit_sec"] = ismc_limit or (12 * 60)
        level_updates["min_questions"] = max(created or 1, 1)
    db.update_item(pk, keys.level_sk(level_id), level_updates)
    return {
        "subject_id": subject_id,
        "level_id": level_id,
        "imported": created,
        "cleared": cleared,
        "replaced": replace,
        "question_count": new_count,
        "exam_kind": exam_kind or (level.get("exam_kind") or ""),
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
            "qtype": i.get("qtype") or "open",
            "choices": i.get("choices") or [],
            "points": int(i.get("points") or 1),
            "penalty": int(i.get("penalty") or 0),
            "item_no": int(i.get("item_no") or 0),
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

    seed_id = "math"
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
        found = find_live_topic("Mathematics", "Arithmetic")
        if found and found.get("subject_id"):
            seed_id = str(found["subject_id"])
        else:
            try:
                get_subject("math")
            except SubjectNotFound:
                return {
                    "subject_created": False,
                    "levels_created": 0,
                    "questions_imported": 0,
                }
            seed_id = "math"

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
                seed_id,
                LevelCreate(
                    level_id=lid,
                    name=name,
                    order=order,
                    pass_accuracy=0.8,
                    min_questions=5,
                ),
            )
            levels_created += 1
            summary = import_questions_csv(seed_id, lid, csv_body)
            questions_imported += summary["imported"]
        except ConflictError:
            # Level exists; only import if empty
            level = get_level(seed_id, lid)
            if int(level.get("question_count") or 0) == 0:
                summary = import_questions_csv(seed_id, lid, csv_body)
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
        "content_kind": item.get("content_kind") or "",
        "presentation_ready": bool(item.get("presentation_ready")),
        "page_count": int(item.get("page_count") or 0),
        "science_ready": bool(item.get("science_ready")),
        "summary": item.get("summary") or "",
        "explainer_video_url": item.get("explainer_video_url") or "",
        "facebook_video_url": item.get("facebook_video_url") or "",
        "image_url": item.get("image_url") or "",
        "interactive_html_url": item.get("html_url")
        or item.get("interactive_html_url")
        or "",
        "created_at": item.get("created_at"),
        **public_playlist_fields(item),
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
        "exam_kind": item.get("exam_kind") or "",
        "time_limit_sec": int(item.get("time_limit_sec") or 0),
        "question_count": int(item.get("question_count") or 0),
        "content_version": int(item.get("content_version") or 0),
        "content_updated_at": item.get("content_updated_at") or item.get("updated_at"),
        "created_at": item.get("created_at"),
    }
