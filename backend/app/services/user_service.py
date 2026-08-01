"""User profile and onboarding service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app import db, keys

TRIAL_DAYS = 30
# Facebook Follow → free subscription window
FB_FOLLOW_SUBSCRIPTION_DAYS = 180  # 6 months
# Comment / feedback on FB every N days keeps ads suppressed (ad banner not built yet)
FB_ENGAGEMENT_AD_FREE_DAYS = 90  # 3 months


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


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
            updated = db.update_item(pk, sk, updates)
            if sid and updates.get("school_id"):
                _try_link_school(sid, user_id)
            return updated
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
    if sid:
        _try_link_school(sid, user_id)
    return item


def _try_link_school(school_id: str, user_id: str) -> None:
    try:
        from app.services import school_service

        school_service.link_user(school_id, user_id)
    except Exception:
        pass


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

        s = school_service.get_school(school_id, allow_pending=True)
        return school_service.display_name_for_item(s)
    except Exception:
        return school_id


def set_school_name(user_id: str, school_id: str, school_name: str) -> dict[str, Any] | None:
    """Admin-driven refresh when a pending school is approved (or renamed)."""
    profile = get_profile(user_id)
    if not profile:
        return None
    current = (profile.get("school_id") or "").strip()
    if current and current != school_id:
        # Do not overwrite if the learner already switched schools
        return profile
    return db.update_item(
        keys.user_pk(user_id),
        keys.user_meta_sk(),
        {
            "school_id": school_id,
            "school_name": school_name,
            "updated_at": _iso(_utcnow()),
        },
    )


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
            # Validate school exists (active or pending request)
            try:
                from app.services import school_service

                school_service.get_school(sid, allow_pending=True)
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
    updated = db.update_item(keys.user_pk(user_id), keys.user_meta_sk(), updates)
    # Track learners on pending schools so approve can refresh profiles
    if school_id is not None:
        sid = str(school_id).strip()
        if sid:
            try:
                from app.services import school_service

                school_service.link_user(sid, user_id)
            except Exception:
                pass
    return updated


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

    Study remains free for all learners. Facebook Follow grants a tracked 6‑month
    free subscription window for Account display / future ad gating.
    """
    del profile, now
    return True


def _subscription_end_active(profile: dict[str, Any], now: datetime) -> bool:
    end = _parse_iso(profile.get("subscription_ends_at"))
    return bool(end and end > now)


def _ad_free_active(profile: dict[str, Any], now: datetime) -> bool:
    """Ads suppressed when user followed and engagement is within 3 months."""
    if not profile.get("facebook_followed"):
        return False
    until = _parse_iso(profile.get("ad_free_until"))
    if until and until > now:
        return True
    # Fallback: engagement timestamp + window
    eng = _parse_iso(profile.get("last_facebook_engagement_at"))
    if eng and eng + timedelta(days=FB_ENGAGEMENT_AD_FREE_DAYS) > now:
        return True
    return False


def facebook_benefits_summary(
    profile: dict[str, Any], now: datetime | None = None
) -> dict[str, Any]:
    """Derived Facebook subscription / ad-free fields for API + Account UI."""
    now = now or _utcnow()
    followed = bool(profile.get("facebook_followed"))
    sub_end = _parse_iso(profile.get("subscription_ends_at"))
    ad_free_until = _parse_iso(profile.get("ad_free_until"))
    last_eng = _parse_iso(profile.get("last_facebook_engagement_at"))
    sub_active = bool(followed and sub_end and sub_end > now)
    ad_free = _ad_free_active(profile, now)
    # Next engagement due = last_eng + 90d, or ad_free_until, or followed_at + 90d
    next_due = None
    if last_eng:
        next_due = last_eng + timedelta(days=FB_ENGAGEMENT_AD_FREE_DAYS)
    elif ad_free_until:
        next_due = ad_free_until
    elif followed:
        followed_at = _parse_iso(profile.get("facebook_followed_at"))
        if followed_at:
            next_due = followed_at + timedelta(days=FB_ENGAGEMENT_AD_FREE_DAYS)
    return {
        "facebook_followed": followed,
        "facebook_followed_at": profile.get("facebook_followed_at") or None,
        "facebook_handle": (profile.get("facebook_handle") or "").strip() or None,
        "facebook_display_name": (profile.get("facebook_display_name") or "").strip()
        or None,
        "facebook_subscription_active": sub_active,
        "facebook_subscription_ends_at": profile.get("subscription_ends_at")
        if followed
        else None,
        "last_facebook_engagement_at": profile.get("last_facebook_engagement_at")
        or None,
        "ad_free_until": profile.get("ad_free_until") or None,
        "ad_free_active": ad_free,
        # Future ad banner: show when not ad-free (not following or engagement stale)
        "ads_may_appear": not ad_free,
        "next_engagement_due_at": _iso(next_due) if next_due else None,
        "engagement_interval_days": FB_ENGAGEMENT_AD_FREE_DAYS,
        "follow_subscription_days": FB_FOLLOW_SUBSCRIPTION_DAYS,
    }


def claim_facebook_follow(
    user_id: str,
    *,
    display_name: str = "",
    handle: str = "",
) -> dict[str, Any]:
    """Honor-system claim after user follows MElon on Facebook.

    Grants 6 months free subscription and starts the 3‑month ad-free window.
    Meta does not allow server-side Follow verification without Graph API OAuth;
    we trust the learner completed Follow in Facebook.
    """
    profile = get_profile(user_id) or ensure_user_profile(user_id)
    now = _utcnow()
    name = (display_name or "").strip()[:80]
    handle = (handle or "").strip()[:200]

    # Subscription end: max(existing future end, now) + 6 months on first claim;
    # re-claim refreshes handle/name but does not stack unlimited years (cap: extend
    # only if current FB subscription already ended or never set).
    already = bool(profile.get("facebook_followed"))
    current_end = _parse_iso(profile.get("subscription_ends_at"))
    if already and current_end and current_end > now:
        # Already following with active window — refresh metadata only, keep end
        sub_end = current_end
    else:
        base = now
        if current_end and current_end > now:
            base = current_end
        sub_end = base + timedelta(days=FB_FOLLOW_SUBSCRIPTION_DAYS)

    ad_free_until = now + timedelta(days=FB_ENGAGEMENT_AD_FREE_DAYS)
    updates: dict[str, Any] = {
        "facebook_followed": True,
        "facebook_followed_at": profile.get("facebook_followed_at") or _iso(now),
        "facebook_display_name": name or profile.get("facebook_display_name") or "",
        "facebook_handle": handle or profile.get("facebook_handle") or "",
        "subscription_status": "active",
        "subscription_ends_at": _iso(sub_end),
        "subscription_source": "facebook_follow",
        "last_facebook_engagement_at": _iso(now),
        "ad_free_until": _iso(ad_free_until),
        "updated_at": _iso(now),
    }
    return db.update_item(keys.user_pk(user_id), keys.user_meta_sk(), updates)


def claim_facebook_engagement(
    user_id: str,
    *,
    kind: str = "comment",
    display_name: str = "",
    text: str = "",
) -> dict[str, Any]:
    """Honor-system claim after user posts FB comment / feedback / feature request.

    Extends ad-free window by 3 months. Does not require prior follow, but
    ads_may_appear stays true until they have followed (ad_free requires follow).
    """
    profile = get_profile(user_id) or ensure_user_profile(user_id)
    now = _utcnow()
    kind = (kind or "comment").strip().lower()
    if kind not in ("comment", "feedback", "feature_request"):
        kind = "comment"
    name = (display_name or "").strip()[:80]
    text = (text or "").strip()[:2000]

    ad_free_until = now + timedelta(days=FB_ENGAGEMENT_AD_FREE_DAYS)
    updates: dict[str, Any] = {
        "last_facebook_engagement_at": _iso(now),
        "last_facebook_engagement_kind": kind,
        "ad_free_until": _iso(ad_free_until),
        "updated_at": _iso(now),
    }
    if name:
        updates["facebook_display_name"] = name
    if text:
        updates["last_facebook_engagement_preview"] = text[:280]
    # Engagement alone does not set facebook_followed; user must claim Follow first
    # for ads_may_appear to become false.
    return db.update_item(keys.user_pk(user_id), keys.user_meta_sk(), updates)


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
    fb = facebook_benefits_summary(profile)
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
        "subscription_source": profile.get("subscription_source") or None,
        "total_study_ms": int(profile.get("total_study_ms") or 0),
        "study_sessions_count": int(profile.get("study_sessions_count") or 0),
        "last_study_elapsed_ms": int(profile.get("last_study_elapsed_ms") or 0),
        "last_study_at": profile.get("last_study_at") or None,
        "last_study_accuracy": profile.get("last_study_accuracy"),
        "last_study_passed": profile.get("last_study_passed"),
        **fb,
    }
    if include_content_notices and profile.get("user_id"):
        from app.services import study_service

        out["content_notices"] = study_service.list_content_notices(profile["user_id"])
    else:
        out["content_notices"] = []
    return out
