"""User profile and onboarding service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app import db, keys

TRIAL_DAYS = 30


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_user_profile(
    user_id: str,
    email: str | None = None,
    nickname: str | None = None,
    school_id: str | None = None,
    grade: str | None = None,
) -> dict[str, Any]:
    """Create profile on first access; return existing otherwise.

    Nickname / school / grade may be backfilled later from Cognito or PATCH /me.
    """
    pk = keys.user_pk(user_id)
    sk = keys.user_meta_sk()
    existing = db.get_item(pk, sk)
    if existing and not existing.get("deleted_at"):
        # Backfill email / nickname / school / grade if profile was created without them
        updates: dict[str, Any] = {}
        if email and not (existing.get("email") or "").strip():
            updates["email"] = email
        nick = (nickname or "").strip()
        if nick and not (existing.get("nickname") or "").strip():
            updates["nickname"] = nick
        sid = (school_id or "").strip()
        if sid and not (existing.get("school_id") or "").strip():
            updates["school_id"] = sid
            updates["school_name"] = _resolve_school_name(sid)
        g = (grade or "").strip()
        if g and not (existing.get("grade") or "").strip():
            updates["grade"] = g
        if updates:
            updates["updated_at"] = _iso(_utcnow())
            return db.update_item(pk, sk, updates)
        return existing

    now = _utcnow()
    trial_end = now + timedelta(days=TRIAL_DAYS)
    sid = (school_id or "").strip()
    item = {
        "PK": pk,
        "SK": sk,
        "entity_type": "USER",
        "user_id": user_id,
        "email": email or "",
        "nickname": (nickname or "").strip(),
        "school_id": sid,
        "school_name": _resolve_school_name(sid) if sid else "",
        "grade": (grade or "").strip(),
        "created_at": _iso(now),
        "updated_at": _iso(now),
        "trial_ends_at": _iso(trial_end),
        # Kept for future billing; free access is always on for now
        "subscription_status": "active",
        "subscription_ends_at": _iso(trial_end),
        # Study stats (updated when sessions complete)
        "total_study_ms": 0,
        "study_sessions_count": 0,
        "last_study_elapsed_ms": 0,
        "last_study_at": "",
        "deleted_at": "",
    }
    try:
        db.put_item(item, condition="attribute_not_exists(PK)")
    except Exception:
        # Race: another request created it
        existing = db.get_item(pk, sk)
        if existing:
            return existing
        raise
    return item


def get_profile(user_id: str) -> dict[str, Any] | None:
    item = db.get_item(keys.user_pk(user_id), keys.user_meta_sk())
    if not item or item.get("deleted_at"):
        return None
    return item


def _resolve_school_name(school_id: str) -> str:
    if not school_id:
        return ""
    try:
        from app.services import school_service

        s = school_service.get_school(school_id)
        name = (s.get("name") or "").strip()
        city = (s.get("city") or "").strip()
        province = (s.get("province") or "").strip()
        loc = ", ".join(p for p in (city, province) if p)
        return f"{name} ({loc})" if loc else name
    except Exception:
        return school_id


def update_profile(
    user_id: str,
    *,
    nickname: str | None = None,
    school_id: str | None = None,
    grade: str | None = None,
) -> dict[str, Any]:
    """Update learner profile fields (nickname, school, grade)."""
    get_profile(user_id) or ensure_user_profile(user_id)
    updates: dict[str, Any] = {"updated_at": _iso(_utcnow())}
    if nickname is not None:
        nick = str(nickname).strip()
        if not nick:
            raise ValueError("nickname cannot be empty")
        if len(nick) > 40:
            raise ValueError("nickname must be at most 40 characters")
        updates["nickname"] = nick
    if school_id is not None:
        sid = str(school_id).strip()
        if sid:
            # Validate school exists
            try:
                from app.services import school_service

                school_service.get_school(sid)
            except Exception as exc:
                raise ValueError(f"Unknown school_id '{sid}'") from exc
            updates["school_id"] = sid
            updates["school_name"] = _resolve_school_name(sid)
        else:
            updates["school_id"] = ""
            updates["school_name"] = ""
    if grade is not None:
        g = str(grade).strip()
        if len(g) > 40:
            raise ValueError("grade must be at most 40 characters")
        updates["grade"] = g
    return db.update_item(keys.user_pk(user_id), keys.user_meta_sk(), updates)


def update_subscription(
    user_id: str,
    *,
    status: str,
    subscription_ends_at: str | None = None,
) -> dict[str, Any]:
    updates: dict[str, Any] = {
        "subscription_status": status,
        "updated_at": _iso(_utcnow()),
    }
    if subscription_ends_at is not None:
        updates["subscription_ends_at"] = subscription_ends_at
    return db.update_item(keys.user_pk(user_id), keys.user_meta_sk(), updates)


def is_subscription_active(profile: dict[str, Any], now: datetime | None = None) -> bool:
    """Access check for study/tasks/insights.

    Subscription / trial product is not implemented yet — all learners have free
    access. Profile still stores subscription_* fields for a future billing phase.
    """
    del profile, now  # reserved for future trial/paid gates
    return True


def record_study_session(
    user_id: str,
    *,
    elapsed_ms: int,
    accuracy: float | None = None,
    passed: bool | None = None,
) -> dict[str, Any]:
    """Accumulate study time and session count on the user profile."""
    profile = get_profile(user_id) or ensure_user_profile(user_id)
    total = int(profile.get("total_study_ms") or 0) + max(0, int(elapsed_ms))
    count = int(profile.get("study_sessions_count") or 0) + 1
    updates: dict[str, Any] = {
        "total_study_ms": total,
        "study_sessions_count": count,
        "last_study_elapsed_ms": max(0, int(elapsed_ms)),
        "last_study_at": _iso(_utcnow()),
        "updated_at": _iso(_utcnow()),
    }
    if accuracy is not None:
        updates["last_study_accuracy"] = accuracy
    if passed is not None:
        updates["last_study_passed"] = passed
    return db.update_item(keys.user_pk(user_id), keys.user_meta_sk(), updates)


def public_profile(profile: dict[str, Any], *, include_content_notices: bool = True) -> dict[str, Any]:
    """Strip internal keys for API response."""
    active = is_subscription_active(profile)
    nickname = (profile.get("nickname") or "").strip() or None
    school_id = (profile.get("school_id") or "").strip() or None
    school_name = (profile.get("school_name") or "").strip() or None
    if school_id and not school_name:
        school_name = _resolve_school_name(school_id) or None
    grade = (profile.get("grade") or "").strip() or None
    out = {
        "user_id": profile.get("user_id"),
        "email": profile.get("email"),
        "nickname": nickname,
        "display_name": nickname,  # alias for clients
        "school_id": school_id,
        "school_name": school_name,
        "grade": grade,
        "created_at": profile.get("created_at"),
        "trial_ends_at": profile.get("trial_ends_at"),
        "subscription_status": profile.get("subscription_status"),
        "subscription_ends_at": profile.get("subscription_ends_at"),
        "subscription_active": active,
        "total_study_ms": int(profile.get("total_study_ms") or 0),
        "study_sessions_count": int(profile.get("study_sessions_count") or 0),
        "last_study_elapsed_ms": int(profile.get("last_study_elapsed_ms") or 0),
        "last_study_at": profile.get("last_study_at") or None,
        "last_study_accuracy": profile.get("last_study_accuracy"),
        "last_study_passed": profile.get("last_study_passed"),
    }
    if include_content_notices and profile.get("user_id"):
        from app.services import study_service

        out["content_notices"] = study_service.list_content_notices(profile["user_id"])
    else:
        out["content_notices"] = []
    return out
