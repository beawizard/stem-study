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


def ensure_user_profile(user_id: str, email: str | None = None) -> dict[str, Any]:
    """Create profile on first access with trial period; return existing otherwise."""
    pk = keys.user_pk(user_id)
    sk = keys.user_meta_sk()
    existing = db.get_item(pk, sk)
    if existing and not existing.get("deleted_at"):
        return existing

    now = _utcnow()
    trial_end = now + timedelta(days=TRIAL_DAYS)
    item = {
        "PK": pk,
        "SK": sk,
        "entity_type": "USER",
        "user_id": user_id,
        "email": email or "",
        "created_at": _iso(now),
        "updated_at": _iso(now),
        "trial_ends_at": _iso(trial_end),
        "subscription_status": "trial",  # trial | active | expired | pending_payment
        "subscription_ends_at": _iso(trial_end),
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
    """Return True if user is within trial or paid active period."""
    now = now or _utcnow()
    status = profile.get("subscription_status", "expired")
    if status in ("trial", "active"):
        ends = profile.get("subscription_ends_at") or profile.get("trial_ends_at")
        if not ends:
            return status == "active"
        try:
            end_dt = datetime.fromisoformat(ends.replace("Z", "+00:00"))
        except ValueError:
            return False
        return now <= end_dt
    return False


def public_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Strip internal keys for API response."""
    active = is_subscription_active(profile)
    return {
        "user_id": profile.get("user_id"),
        "email": profile.get("email"),
        "created_at": profile.get("created_at"),
        "trial_ends_at": profile.get("trial_ends_at"),
        "subscription_status": profile.get("subscription_status"),
        "subscription_ends_at": profile.get("subscription_ends_at"),
        "subscription_active": active,
    }
