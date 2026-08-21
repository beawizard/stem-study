"""Mastery collections — curated topic packs that reuse Study sessions/XP."""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from typing import Any

from app import db, keys
from app.validation import MasteryCreate


class MasteryNotFound(Exception):
    pass


class MasteryForbidden(Exception):
    pass


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return uuid.uuid4().hex


def _base_topic_name(topic_or_name: str) -> str:
    """Strip trailing ' - Level N…' so Level variants collapse to one topic."""
    s = (topic_or_name or "").strip()
    if not s:
        return ""
    return re.sub(
        r"\s*[-–—]\s*Level\s*\d+.*$",
        "",
        s,
        flags=re.IGNORECASE,
    ).strip() or s


def resolve_subject_ids_for_topics(category: str, topics: list[str]) -> list[str]:
    """Map base topic names under a category to all matching subject_ids."""
    from app.services import subject_service

    wanted = {t.strip().lower() for t in topics if (t or "").strip()}
    if not wanted:
        return []
    cat = (category or "").strip()
    ids: list[str] = []
    seen: set[str] = set()
    for s in subject_service.list_subjects():
        if (s.get("category") or "Mathematics") != cat:
            continue
        base = _base_topic_name(s.get("topic") or s.get("name") or "")
        if base.lower() not in wanted:
            continue
        sid = s.get("subject_id")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        ids.append(sid)
    return ids


def create_mastery(
    user_id: str,
    data: MasteryCreate,
    *,
    is_admin: bool = False,
) -> dict[str, Any]:
    """Create and publish a mastery collection for the learner (or shared if admin)."""
    shared = bool(data.shared) and bool(is_admin)
    subject_ids = list(data.subject_ids or [])
    if not subject_ids:
        subject_ids = resolve_subject_ids_for_topics(data.category, data.topics)
    if len(subject_ids) < 1:
        raise ValueError(
            "No study subjects match the selected topics in this category"
        )

    mastery_id = _new_id()
    now = _utcnow_iso()
    item: dict[str, Any] = {
        "PK": keys.user_pk(user_id),
        "SK": keys.mastery_sk(mastery_id),
        "entity_type": "MASTERY",
        "mastery_id": mastery_id,
        "user_id": user_id,
        "name": data.name.strip(),
        "category": data.category,
        "topics": list(data.topics),
        "subject_ids": subject_ids,
        "start_date": data.start_date,
        "end_date": data.end_date,
        "status": "published",
        "shared": shared,
        "created_at": now,
        "updated_at": now,
        "published_at": now,
        "deleted_at": "",
    }
    if shared:
        item["GSI1PK"] = keys.ENTITY_MASTERY_SHARED
        item["GSI1SK"] = mastery_id
    db.put_item(item)
    return _public(item, viewer_id=user_id)


def get_mastery(user_id: str, mastery_id: str) -> dict[str, Any]:
    """Load a collection the viewer owns or that is admin-shared."""
    own = db.get_item(keys.user_pk(user_id), keys.mastery_sk(mastery_id))
    if own and not own.get("deleted_at") and own.get("entity_type") == "MASTERY":
        return _public(own, viewer_id=user_id)

    shared_rows = db.query_gsi1(
        keys.ENTITY_MASTERY_SHARED, sk_begins_with=mastery_id, limit=5
    )
    for item in shared_rows:
        if item.get("deleted_at"):
            continue
        if item.get("mastery_id") != mastery_id:
            continue
        if not item.get("shared"):
            continue
        if item.get("status") != "published":
            continue
        return _public(item, viewer_id=user_id)

    raise MasteryNotFound(f"Mastery {mastery_id} not found")


def list_mastery_for_user(user_id: str) -> list[dict[str, Any]]:
    """Published personal collections + admin-shared packs for the hub menu."""
    by_id: dict[str, dict[str, Any]] = {}

    own_items = db.query_pk(keys.user_pk(user_id), sk_begins_with="MASTERY#")
    for item in own_items:
        if item.get("deleted_at"):
            continue
        if item.get("entity_type") and item.get("entity_type") != "MASTERY":
            continue
        if item.get("status") != "published":
            continue
        mid = item.get("mastery_id")
        if mid:
            by_id[mid] = _public(item, viewer_id=user_id)

    for item in db.query_gsi1(keys.ENTITY_MASTERY_SHARED):
        if item.get("deleted_at"):
            continue
        if not item.get("shared"):
            continue
        if item.get("status") != "published":
            continue
        # Skip own already listed
        mid = item.get("mastery_id")
        if not mid or mid in by_id:
            continue
        by_id[mid] = _public(item, viewer_id=user_id)

    rows = list(by_id.values())
    rows.sort(key=lambda m: m.get("published_at") or m.get("created_at") or "", reverse=True)
    return rows


def soft_delete_mastery(user_id: str, mastery_id: str, *, is_admin: bool = False) -> None:
    """Owner (or admin for shared) soft-deletes a collection."""
    own = db.get_item(keys.user_pk(user_id), keys.mastery_sk(mastery_id))
    if own and not own.get("deleted_at"):
        if own.get("user_id") != user_id and not is_admin:
            raise MasteryForbidden("Not mastery owner")
        db.update_item(
            keys.user_pk(own["user_id"]),
            keys.mastery_sk(mastery_id),
            {
                "deleted_at": _utcnow_iso(),
                "updated_at": _utcnow_iso(),
                "status": "deleted",
                "shared": False,
                # Move off the shared index (empty GSI keys are invalid in DynamoDB)
                "GSI1PK": "ENTITY#MASTERY_DELETED",
                "GSI1SK": mastery_id,
            },
        )
        return

    # Admin deleting someone else's shared pack via GSI lookup
    if not is_admin:
        raise MasteryNotFound(f"Mastery {mastery_id} not found")
    shared_rows = db.query_gsi1(
        keys.ENTITY_MASTERY_SHARED, sk_begins_with=mastery_id, limit=5
    )
    for item in shared_rows:
        if item.get("mastery_id") != mastery_id:
            continue
        owner = item.get("user_id")
        if not owner:
            continue
        db.update_item(
            keys.user_pk(owner),
            keys.mastery_sk(mastery_id),
            {
                "deleted_at": _utcnow_iso(),
                "updated_at": _utcnow_iso(),
                "status": "deleted",
                "shared": False,
                "GSI1PK": "ENTITY#MASTERY_DELETED",
                "GSI1SK": mastery_id,
            },
        )
        return
    raise MasteryNotFound(f"Mastery {mastery_id} not found")


def _parse_ymd(value: str | None) -> date | None:
    if not value:
        return None
    try:
        y, m, d = (int(x) for x in str(value).split("-")[:3])
        return date(y, m, d)
    except (TypeError, ValueError):
        return None


def window_status(start_date: str | None, end_date: str | None, *, today: date | None = None) -> str:
    """active | upcoming | ended based on calendar dates (inclusive)."""
    today = today or datetime.now(timezone.utc).date()
    start = _parse_ymd(start_date)
    end = _parse_ymd(end_date)
    if start and today < start:
        return "upcoming"
    if end and today > end:
        return "ended"
    return "active"


def _public(item: dict[str, Any], *, viewer_id: str | None = None) -> dict[str, Any]:
    start = item.get("start_date") or ""
    end = item.get("end_date") or ""
    owner = item.get("user_id")
    return {
        "mastery_id": item.get("mastery_id"),
        "name": item.get("name"),
        "category": item.get("category"),
        "topics": list(item.get("topics") or []),
        "subject_ids": list(item.get("subject_ids") or []),
        "start_date": start or None,
        "end_date": end or None,
        "status": item.get("status") or "published",
        "shared": bool(item.get("shared")),
        "window_status": window_status(start, end),
        "is_owner": bool(viewer_id and owner == viewer_id),
        "owner_id": owner,
        "created_at": item.get("created_at"),
        "published_at": item.get("published_at") or item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }
