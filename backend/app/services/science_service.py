"""Science topics — summary, cover image, and explainer video URL."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import boto3

from app import db, keys
from app.services.subject_service import (
    ConflictError,
    SubjectNotFound,
    create_subject,
    get_subject,
    slugify_subject_id,
    update_subject,
    _public_subject,
)
from app.validation import MAX_DESC_LEN, SubjectCreate, SubjectUpdate, ScienceTopicCreate

ALLOWED_IMAGE_EXT = {"jpg", "jpeg", "png", "webp", "gif", "bmp"}


class ScienceError(Exception):
    pass


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bucket() -> str:
    name = (os.environ.get("FRONTEND_BUCKET") or "").strip()
    if not name:
        raise ScienceError("FRONTEND_BUCKET is not configured")
    return name


def _public_base() -> str:
    base = (os.environ.get("FRONTEND_PUBLIC_BASE") or "").strip().rstrip("/")
    if base:
        return base
    bucket = _bucket()
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-southeast-1"
    return f"http://{bucket}.s3-website-{region}.amazonaws.com"


def _s3():
    return boto3.client("s3", region_name=os.environ.get("AWS_REGION", "ap-southeast-1"))


def _safe_ext(raw: str, allowed: set[str], default: str) -> str:
    ext = re.sub(r"[^a-z0-9]", "", (raw or "").lower().lstrip("."))
    if ext == "jpeg":
        ext = "jpg"
    if ext in allowed:
        return ext
    return default


def _object_url(key: str) -> str:
    return f"{_public_base()}/{quote(key)}"


def _cover_key(subject_id: str, image_ext: str) -> str:
    return f"science/{subject_id}/cover.{image_ext}"


def start_topic_upload(data: ScienceTopicCreate, *, admin_user_id: str) -> dict[str, Any]:
    """Create/replace a Science topic and optionally return a presigned image PUT."""
    topic = data.topic.strip()
    subject_id = slugify_subject_id("Science", topic)
    pk = keys.subject_pk(subject_id)
    existing = db.get_item(pk, keys.subject_meta_sk())
    live = bool(existing and not existing.get("deleted_at"))
    if live and not data.replace:
        raise ConflictError(
            f"Topic '{topic}' already exists. Enable replace to overwrite."
        )

    summary = (data.summary or "").strip()
    video = (data.explainer_video_url or "").strip()
    desc = summary[:MAX_DESC_LEN]

    if existing:
        update_subject(
            subject_id,
            SubjectUpdate(
                category="Science",
                topic=topic,
                description=desc or existing.get("description") or "",
                grade_level=data.grade_level,
            ),
        )
    else:
        create_subject(
            SubjectCreate(
                category="Science",
                topic=topic,
                subject_id=subject_id,
                description=desc,
                grade_level=data.grade_level,
                sort_order=400,
            )
        )

    has_image = bool((data.image_ext or "").strip())
    image_key = (existing or {}).get("image_key") or ""
    image_url = (existing or {}).get("image_url") or ""
    image_put_url = ""
    image_ct = (data.image_content_type or "").strip() or "image/jpeg"

    if has_image:
        img_ext = _safe_ext(data.image_ext, ALLOWED_IMAGE_EXT, "jpg")
        image_key = _cover_key(subject_id, img_ext)
        image_url = _object_url(image_key)
        s3 = _s3()
        bucket = _bucket()
        image_put_url = s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": bucket,
                "Key": image_key,
                "ContentType": image_ct,
            },
            ExpiresIn=3600,
        )
    elif not image_key:
        raise ScienceError("Cover image is required")

    now = _utcnow_iso()
    db.update_item(
        pk,
        keys.subject_meta_sk(),
        {
            "content_kind": "science",
            "science_ready": False,
            "summary": summary,
            "explainer_video_url": video,
            "image_key": image_key,
            "image_url": image_url,
            "grade_level": data.grade_level or "",
            "uploaded_by": admin_user_id,
            "updated_at": now,
        },
    )
    pub = public_science_topic(get_subject(subject_id))
    return {
        **pub,
        "subject_id": subject_id,
        "image_put_url": image_put_url,
        "image_content_type": image_ct if image_put_url else "",
    }


def complete_topic_upload(subject_id: str) -> dict[str, Any]:
    item = get_subject(subject_id)
    if (item.get("category") or "") != "Science":
        raise ScienceError("Not a Science topic")
    updated = db.update_item(
        keys.subject_pk(subject_id),
        keys.subject_meta_sk(),
        {
            "science_ready": True,
            "updated_at": _utcnow_iso(),
        },
    )
    return public_science_topic(updated)


def public_science_topic(item: dict[str, Any]) -> dict[str, Any]:
    img_key = item.get("image_key") or ""
    base = _public_subject(item)
    base.update(
        {
            "content_kind": item.get("content_kind") or "science",
            "science_ready": bool(item.get("science_ready")),
            "summary": item.get("summary") or item.get("description") or "",
            "explainer_video_url": item.get("explainer_video_url") or "",
            "image_url": item.get("image_url") or (_object_url(img_key) if img_key else ""),
        }
    )
    return base


def get_science_topic(subject_id: str) -> dict[str, Any]:
    item = get_subject(subject_id)
    if (item.get("category") or "") != "Science":
        raise SubjectNotFound(f"Science topic '{subject_id}' not found")
    return public_science_topic(item)


def list_science_topics() -> list[dict[str, Any]]:
    from app.services.subject_service import list_subjects

    rows = []
    for s in list_subjects():
        if (s.get("category") or "") != "Science":
            continue
        rows.append(s)
    return rows
