"""School catalog (admin-managed) for learner profiles and sign-up."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from app import db, keys


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


def _public(item: dict[str, Any]) -> dict[str, Any]:
    name = (item.get("name") or "").strip()
    city = (item.get("city") or "").strip()
    province = (item.get("province") or "").strip()
    loc_parts = [p for p in (city, province) if p]
    location = ", ".join(loc_parts)
    label = f"{name} ({location})" if location else name
    return {
        "school_id": item.get("school_id"),
        "name": name,
        "city": city,
        "province": province,
        "location": location,
        "label": label,
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }


def list_schools() -> list[dict[str, Any]]:
    items = db.query_gsi1(keys.ENTITY_SCHOOL)
    active = [i for i in items if not i.get("deleted_at")]
    active.sort(key=lambda s: ((s.get("name") or "").lower(), s.get("school_id") or ""))
    return [_public(i) for i in active]


def get_school(school_id: str) -> dict[str, Any]:
    item = db.get_item(keys.school_pk(school_id), keys.school_meta_sk())
    if not item or item.get("deleted_at"):
        raise SchoolNotFound(f"School '{school_id}' not found")
    return item


def create_school(
    *,
    name: str,
    city: str = "",
    province: str = "",
    school_id: str | None = None,
) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise ValueError("name is required")
    city = (city or "").strip()
    province = (province or "").strip()

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
        "created_at": now,
        "updated_at": now,
        "deleted_at": "",
    }
    try:
        db.put_item(item, condition="attribute_not_exists(PK)")
    except Exception as exc:
        raise SchoolConflict(f"School '{sid}' already exists") from exc
    return _public(item)


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
    return _public(updated)


def soft_delete_school(school_id: str) -> None:
    get_school(school_id)
    db.update_item(
        keys.school_pk(school_id),
        keys.school_meta_sk(),
        {"deleted_at": _iso(_utcnow()), "updated_at": _iso(_utcnow())},
    )
