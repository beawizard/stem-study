"""Per-topic learner access/usage for Admin (top 100 by time)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app import db, keys
from app.services import subject_service, user_service
from app.services.subject_service import SubjectNotFound

# Cap a single ping so a stuck tab cannot dump hours in one request.
MAX_PING_MS = 300_000
USAGE_TOP_N = 100


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _display_name(profile: dict[str, Any] | None, user_id: str) -> str:
    if profile:
        nick = str(profile.get("nickname") or "").strip()
        if nick:
            return nick
        email = str(profile.get("email") or "").strip()
        if email and "@" in email:
            return email.split("@", 1)[0]
        if email:
            return email
    return "Learner"


def _grade(profile: dict[str, Any] | None) -> str:
    if not profile:
        return ""
    return str(profile.get("grade") or "").strip()


def _mins(total_ms: int) -> float:
    return round(max(0, int(total_ms or 0)) / 60_000, 1)


def record_topic_access(user_id: str, subject_id: str, elapsed_ms: int = 0) -> dict[str, Any]:
    """Create/update USER#… / ACCESS#<subject> and GSI rank for that topic."""
    sid = str(subject_id or "").strip()
    if not sid:
        raise ValueError("subject_id is required")
    # Validate topic exists (also 404s deleted subjects)
    subject_service.get_subject(sid)

    added = max(0, min(int(elapsed_ms or 0), MAX_PING_MS))
    now = _utcnow_iso()
    pk = keys.user_pk(user_id)
    sk = keys.access_sk(sid)
    existing = db.get_item(pk, sk)
    profile = user_service.get_profile(user_id) or {}
    nickname = _display_name(profile, user_id)
    grade = _grade(profile)

    if existing and not existing.get("deleted_at"):
        total = int(existing.get("total_ms") or 0) + added
        first = str(existing.get("first_access_at") or "") or now
    else:
        total = added
        first = now

    item = {
        "PK": pk,
        "SK": sk,
        "entity_type": "TOPIC_ACCESS",
        "user_id": user_id,
        "subject_id": sid,
        "nickname": nickname,
        "grade_level": grade,
        "total_ms": total,
        "first_access_at": first,
        "last_access_at": now,
        "updated_at": now,
        "deleted_at": "",
        "GSI1PK": keys.topic_access_gsi1_pk(sid),
        "GSI1SK": keys.topic_access_gsi1_sk(total, user_id),
    }
    db.put_item(item)
    return item


def list_topic_usage(subject_id: str, *, limit: int = USAGE_TOP_N) -> dict[str, Any]:
    """Admin: top learners by accumulated minutes on a topic."""
    sid = str(subject_id or "").strip()
    if not sid:
        raise SubjectNotFound("Topic not found")
    subject = subject_service.get_subject(sid)
    n = max(1, min(int(limit or USAGE_TOP_N), USAGE_TOP_N))
    raw = db.query_gsi1(keys.topic_access_gsi1_pk(sid), limit=min(n * 2, 200))
    users: list[dict[str, Any]] = []
    for item in raw:
        if item.get("deleted_at"):
            continue
        if (item.get("entity_type") or "TOPIC_ACCESS") != "TOPIC_ACCESS":
            continue
        total_ms = int(item.get("total_ms") or 0)
        users.append(
            {
                "rank": len(users) + 1,
                "user_id": item.get("user_id") or "",
                "nickname": item.get("nickname") or "Learner",
                "grade_level": item.get("grade_level") or "",
                "total_ms": total_ms,
                "total_mins": _mins(total_ms),
                "first_access_at": item.get("first_access_at") or "",
                "last_access_at": item.get("last_access_at") or "",
            }
        )
        if len(users) >= n:
            break
    return {
        "subject_id": sid,
        "topic": subject.get("topic") or subject.get("name") or sid,
        "category": subject.get("category") or "",
        "grade_level": subject.get("grade_level") or "",
        "users": users,
    }
