"""GCash payment recording and admin verification.

Flow (cost-efficient, no payment gateway fees in v1):
1. User's trial expires → subscription_status becomes expired (lazy check).
2. User submits GCash reference number after paying to the merchant number.
3. Admin verifies or rejects the payment.
4. On verify → extend subscription_ends_at by SUBSCRIPTION_DAYS.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app import db, keys
from app.services import user_service
from app.validation import PaymentSubmit

SUBSCRIPTION_DAYS = 30
DEFAULT_AMOUNT_PHP = 99.0


class PaymentError(Exception):
    pass


class PaymentNotFound(PaymentError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def submit_payment(user_id: str, data: PaymentSubmit) -> dict[str, Any]:
    payment_id = uuid.uuid4().hex
    now = _utcnow()
    item = {
        "PK": keys.user_pk(user_id),
        "SK": keys.payment_sk(payment_id),
        "GSI1PK": keys.ENTITY_PAYMENT,
        "GSI1SK": f"{_iso(now)}#{payment_id}",
        "entity_type": "PAYMENT",
        "payment_id": payment_id,
        "user_id": user_id,
        "gcash_reference": data.gcash_reference,
        "amount_php": data.amount_php,
        "notes": data.notes or "",
        "status": "pending",  # pending | verified | rejected
        "created_at": _iso(now),
        "updated_at": _iso(now),
        "verified_at": "",
        "verified_by": "",
        "deleted_at": "",
    }
    db.put_item(item)
    user_service.update_subscription(user_id, status="pending_payment")
    return _public(item)


def get_payment(user_id: str, payment_id: str) -> dict[str, Any]:
    item = db.get_item(keys.user_pk(user_id), keys.payment_sk(payment_id))
    if not item or item.get("deleted_at"):
        raise PaymentNotFound(f"Payment {payment_id} not found")
    return item


def list_user_payments(user_id: str) -> list[dict[str, Any]]:
    items = db.query_pk(keys.user_pk(user_id), sk_begins_with="PAYMENT#")
    return [_public(i) for i in items if not i.get("deleted_at")]


def list_pending_payments(limit: int = 100) -> list[dict[str, Any]]:
    items = db.query_gsi1(keys.ENTITY_PAYMENT, limit=limit)
    return [
        _public(i)
        for i in items
        if not i.get("deleted_at") and i.get("status") == "pending"
    ]


def verify_payment(
    payment_user_id: str,
    payment_id: str,
    *,
    status: str,
    admin_user_id: str,
    notes: str = "",
) -> dict[str, Any]:
    if status not in ("verified", "rejected"):
        raise PaymentError("status must be verified or rejected")

    item = get_payment(payment_user_id, payment_id)
    if item.get("status") != "pending":
        raise PaymentError(f"Payment already {item.get('status')}")

    now = _utcnow()
    updates: dict[str, Any] = {
        "status": status,
        "updated_at": _iso(now),
        "verified_at": _iso(now),
        "verified_by": admin_user_id,
        "admin_notes": notes,
    }
    updated = db.update_item(
        keys.user_pk(payment_user_id),
        keys.payment_sk(payment_id),
        updates,
    )

    if status == "verified":
        ends = now + timedelta(days=SUBSCRIPTION_DAYS)
        # Extend from current end if still active
        profile = user_service.get_profile(payment_user_id)
        if profile:
            current_end = profile.get("subscription_ends_at")
            if current_end:
                try:
                    cur = datetime.fromisoformat(current_end.replace("Z", "+00:00"))
                    if cur > now:
                        ends = cur + timedelta(days=SUBSCRIPTION_DAYS)
                except ValueError:
                    pass
        user_service.update_subscription(
            payment_user_id,
            status="active",
            subscription_ends_at=_iso(ends),
        )
    else:
        # Rejected — mark expired if trial already over
        profile = user_service.get_profile(payment_user_id)
        if profile and not user_service.is_subscription_active(profile):
            user_service.update_subscription(payment_user_id, status="expired")

    return _public(updated)


def refresh_subscription_status(user_id: str) -> dict[str, Any]:
    """Lazy-expire trial/active subscriptions past their end date."""
    profile = user_service.get_profile(user_id)
    if not profile:
        profile = user_service.ensure_user_profile(user_id)

    if profile.get("subscription_status") in ("trial", "active"):
        if not user_service.is_subscription_active(profile):
            profile = user_service.update_subscription(user_id, status="expired")
    return profile


def _public(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "payment_id": item.get("payment_id"),
        "user_id": item.get("user_id"),
        "gcash_reference": item.get("gcash_reference"),
        "amount_php": float(item.get("amount_php") or 0),
        "notes": item.get("notes", ""),
        "status": item.get("status"),
        "created_at": item.get("created_at"),
        "verified_at": item.get("verified_at") or None,
        "admin_notes": item.get("admin_notes", ""),
    }
