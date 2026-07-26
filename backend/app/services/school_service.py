"""School catalog (admin-managed) for learner profiles and sign-up.

Learners may request a school that is not listed yet. Requests create a
**pending** school (temporary name) assigned at sign-up; admins approve to
activate the school and refresh linked user profiles. Best-effort email
notifies ADMIN_NOTIFY_EMAIL via SES when configured.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from app import db, keys

logger = logging.getLogger(__name__)

STATUS_ACTIVE = "active"
STATUS_PENDING = "pending"
TEMP_NAME_SUFFIX = " (pending)"


class SchoolNotFound(Exception):
    pass


class SchoolConflict(Exception):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify_school_id(name: str) -> str:
    raw = re.sub(r"[^a-z0-9]+", "-", (name or "").lower().strip())
    raw = re.sub(r"-+", "-", raw).strip("-")
    if not raw:
        raw = "school"
    return raw[:48]


def _location(city: str, province: str) -> str:
    return ", ".join(p for p in ((city or "").strip(), (province or "").strip()) if p)


def _display_name(name: str, city: str = "", province: str = "", *, pending: bool = False) -> str:
    base = (name or "").strip()
    loc = _location(city, province)
    label = f"{base} ({loc})" if loc else base
    if pending:
        # Temporary school name until admin approves
        if not label.endswith(TEMP_NAME_SUFFIX.strip()):
            label = f"{label}{TEMP_NAME_SUFFIX}"
    return label


def _public(item: dict[str, Any], *, include_admin: bool = False) -> dict[str, Any]:
    name = (item.get("name") or "").strip()
    city = (item.get("city") or "").strip()
    province = (item.get("province") or "").strip()
    status = (item.get("status") or STATUS_ACTIVE).strip() or STATUS_ACTIVE
    pending = status == STATUS_PENDING
    location = _location(city, province)
    label = _display_name(name, city, province, pending=pending)
    out: dict[str, Any] = {
        "school_id": item.get("school_id"),
        "name": name,
        "city": city,
        "province": province,
        "location": location,
        "label": label,
        "status": status,
        "pending": pending,
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }
    if include_admin:
        out["requester_email"] = (item.get("requester_email") or "").strip() or None
        out["linked_user_ids"] = list(item.get("linked_user_ids") or [])
        out["approved_at"] = item.get("approved_at") or None
    return out


def list_schools(*, include_pending: bool = False) -> list[dict[str, Any]]:
    """Public catalog: active schools only. Admin may pass include_pending."""
    items = db.query_gsi1(keys.ENTITY_SCHOOL)
    active = [i for i in items if not i.get("deleted_at")]
    if not include_pending:
        active = [
            i
            for i in active
            if (i.get("status") or STATUS_ACTIVE) != STATUS_PENDING
        ]
    active.sort(key=lambda s: ((s.get("name") or "").lower(), s.get("school_id") or ""))
    return [_public(i, include_admin=include_pending) for i in active]


def get_school(school_id: str, *, allow_pending: bool = True) -> dict[str, Any]:
    item = db.get_item(keys.school_pk(school_id), keys.school_meta_sk())
    if not item or item.get("deleted_at"):
        raise SchoolNotFound(f"School '{school_id}' not found")
    status = (item.get("status") or STATUS_ACTIVE).strip() or STATUS_ACTIVE
    if status == STATUS_PENDING and not allow_pending:
        raise SchoolNotFound(f"School '{school_id}' not found")
    return item


def create_school(
    *,
    name: str,
    city: str = "",
    province: str = "",
    school_id: str | None = None,
    status: str = STATUS_ACTIVE,
    requester_email: str = "",
) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise ValueError("name is required")
    city = (city or "").strip()
    province = (province or "").strip()
    status = (status or STATUS_ACTIVE).strip().lower()
    if status not in (STATUS_ACTIVE, STATUS_PENDING):
        raise ValueError("invalid status")

    sid = (school_id or "").strip() or slugify_school_id(name)
    sid = re.sub(r"[^a-zA-Z0-9_-]", "-", sid)[:64]
    if not re.match(r"^[a-zA-Z0-9]", sid):
        sid = f"s-{sid}"
    # Avoid collision
    existing = db.get_item(keys.school_pk(sid), keys.school_meta_sk())
    if existing and not existing.get("deleted_at"):
        sid = f"{sid[:40]}-{uuid.uuid4().hex[:6]}"

    now = _iso(_utcnow())
    item = {
        "PK": keys.school_pk(sid),
        "SK": keys.school_meta_sk(),
        "GSI1PK": keys.ENTITY_SCHOOL,
        "GSI1SK": f"{name.lower()[:80]}#{sid}",
        "entity_type": "SCHOOL",
        "school_id": sid,
        "name": name,
        "city": city,
        "province": province,
        "status": status,
        "requester_email": (requester_email or "").strip().lower(),
        "linked_user_ids": [],
        "created_at": now,
        "updated_at": now,
        "deleted_at": "",
        "approved_at": now if status == STATUS_ACTIVE else "",
    }
    try:
        db.put_item(item, condition="attribute_not_exists(PK)")
    except Exception as exc:
        raise SchoolConflict(f"School '{sid}' already exists") from exc
    return _public(item, include_admin=True)


def request_school(
    *,
    name: str,
    city: str = "",
    province: str = "",
    requester_email: str = "",
) -> dict[str, Any]:
    """Learner-facing: create a pending school and notify admin (best-effort email)."""
    email = (requester_email or "").strip().lower()
    school = create_school(
        name=name,
        city=city,
        province=province,
        status=STATUS_PENDING,
        requester_email=email,
    )
    _notify_admin_school_request(
        school_id=school["school_id"],
        name=school["name"],
        city=school.get("city") or "",
        province=school.get("province") or "",
        requester_email=email,
        label=school.get("label") or school["name"],
    )
    return school


def _notify_admin_school_request(
    *,
    school_id: str,
    name: str,
    city: str,
    province: str,
    requester_email: str,
    label: str,
) -> None:
    admin_email = (os.environ.get("ADMIN_NOTIFY_EMAIL") or "").strip()
    if not admin_email:
        logger.info(
            "School request %s created; ADMIN_NOTIFY_EMAIL not set — skip email",
            school_id,
        )
        return
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-southeast-1"
    source = (os.environ.get("SES_FROM_EMAIL") or admin_email).strip()
    subject = f"[MElon] New school request: {name}"
    lines = [
        "A learner requested a school that is not in the catalog.",
        "",
        f"School name: {name}",
        f"City: {city or '—'}",
        f"Province: {province or '—'}",
        f"Temporary label: {label}",
        f"School id: {school_id}",
        f"Requester email: {requester_email or '—'}",
        "",
        "Open Admin → Schools and approve this request so the learner profile is updated.",
    ]
    body_text = "\n".join(lines)
    try:
        import boto3

        client = boto3.client("ses", region_name=region)
        client.send_email(
            Source=source,
            Destination={"ToAddresses": [admin_email]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Text": {"Data": body_text, "Charset": "UTF-8"}},
            },
        )
        logger.info("School request email sent to %s for %s", admin_email, school_id)
    except Exception:
        # Do not fail the request if email is unavailable (sandbox / identity / IAM)
        logger.exception("Failed to email admin about school request %s", school_id)


def update_school(
    school_id: str,
    *,
    name: str | None = None,
    city: str | None = None,
    province: str | None = None,
) -> dict[str, Any]:
    item = get_school(school_id)
    updates: dict[str, Any] = {"updated_at": _iso(_utcnow())}
    if name is not None:
        n = name.strip()
        if not n:
            raise ValueError("name cannot be empty")
        updates["name"] = n
        updates["GSI1SK"] = f"{n.lower()[:80]}#{school_id}"
    if city is not None:
        updates["city"] = city.strip()
    if province is not None:
        updates["province"] = province.strip()
    updated = db.update_item(keys.school_pk(school_id), keys.school_meta_sk(), updates)
    # Keep linked learner profiles in sync when admin renames an active school
    if (updated.get("status") or STATUS_ACTIVE) == STATUS_ACTIVE:
        _refresh_linked_profiles(school_id, updated)
    return _public(updated, include_admin=True)


def soft_delete_school(school_id: str) -> None:
    get_school(school_id)
    db.update_item(
        keys.school_pk(school_id),
        keys.school_meta_sk(),
        {"deleted_at": _iso(_utcnow()), "updated_at": _iso(_utcnow())},
    )


def link_user(school_id: str, user_id: str) -> None:
    """Remember a learner who selected this school so approve can update them."""
    if not school_id or not user_id:
        return
    try:
        item = get_school(school_id, allow_pending=True)
    except SchoolNotFound:
        return
    linked = list(item.get("linked_user_ids") or [])
    if user_id in linked:
        return
    linked.append(user_id)
    # Bound list size (unlikely to grow large for a single school request)
    linked = linked[-200:]
    db.update_item(
        keys.school_pk(school_id),
        keys.school_meta_sk(),
        {"linked_user_ids": linked, "updated_at": _iso(_utcnow())},
    )


def approve_school(school_id: str) -> dict[str, Any]:
    """Admin approves a pending school request: activate + update requester profiles."""
    item = get_school(school_id, allow_pending=True)
    now = _iso(_utcnow())
    updates: dict[str, Any] = {
        "status": STATUS_ACTIVE,
        "approved_at": now,
        "updated_at": now,
    }
    updated = db.update_item(keys.school_pk(school_id), keys.school_meta_sk(), updates)
    _refresh_linked_profiles(school_id, updated)
    # Also match any user with this school_id who was not yet linked (best-effort by email)
    req_email = (updated.get("requester_email") or "").strip().lower()
    if req_email:
        _link_and_refresh_by_email(school_id, req_email, updated)
    return _public(updated, include_admin=True)


def display_name_for_item(item: dict[str, Any]) -> str:
    status = (item.get("status") or STATUS_ACTIVE).strip() or STATUS_ACTIVE
    return _display_name(
        item.get("name") or "",
        item.get("city") or "",
        item.get("province") or "",
        pending=status == STATUS_PENDING,
    )


def _refresh_linked_profiles(school_id: str, school_item: dict[str, Any]) -> int:
    from app.services import user_service

    name = display_name_for_item(school_item)
    linked = list(school_item.get("linked_user_ids") or [])
    count = 0
    for uid in linked:
        try:
            user_service.set_school_name(uid, school_id, name)
            count += 1
        except Exception:
            logger.exception("Failed to refresh school name for user %s", uid)
    return count


def _link_and_refresh_by_email(
    school_id: str, email: str, school_item: dict[str, Any]
) -> None:
    """If requester already has a profile with this school_id, refresh name.

    Without an email GSI we cannot scan all users cheaply; link_user on
    profile update is the primary path. This is a no-op placeholder for
    symmetry — email is kept for admin UI and notifications.
    """
    del school_id, email, school_item
