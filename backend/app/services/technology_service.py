"""Technology presentation topics — slides + audio + text stored on S3."""

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
from app.validation import SubjectCreate, SubjectUpdate, TechnologyTopicCreate

ALLOWED_IMAGE_EXT = {"jpg", "jpeg", "png", "webp", "gif"}
ALLOWED_AUDIO_EXT = {"mp3", "m4a", "aac", "wav", "ogg"}


class TechnologyError(Exception):
    pass


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bucket() -> str:
    name = (os.environ.get("FRONTEND_BUCKET") or "").strip()
    if not name:
        raise TechnologyError("FRONTEND_BUCKET is not configured")
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


def _page_keys(subject_id: str, index: int, image_ext: str, audio_ext: str) -> tuple[str, str]:
    nn = f"{int(index):02d}"
    img_key = f"technology/{subject_id}/images/{nn}.{image_ext}"
    aud_key = f"technology/{subject_id}/audio/{nn}.{audio_ext}" if audio_ext else ""
    return img_key, aud_key


def start_topic_upload(data: TechnologyTopicCreate, *, admin_user_id: str) -> dict[str, Any]:
    """Create/replace a Technology presentation topic and return presigned PUTs."""
    topic = data.topic.strip()
    subject_id = slugify_subject_id("Technology", topic)
    pk = keys.subject_pk(subject_id)
    existing = db.get_item(pk, keys.subject_meta_sk())
    live = bool(existing and not existing.get("deleted_at"))
    if live and not data.replace:
        raise ConflictError(
            f"Topic '{topic}' already exists. Enable replace to overwrite."
        )

    if existing:
        update_subject(
            subject_id,
            SubjectUpdate(
                category="Technology",
                topic=topic,
                description=data.description or existing.get("description") or "",
                grade_level=data.grade_level,
            ),
        )
    else:
        create_subject(
            SubjectCreate(
                category="Technology",
                topic=topic,
                subject_id=subject_id,
                description=data.description or "",
                grade_level=data.grade_level,
                sort_order=500,
            )
        )

    pages_out: list[dict[str, Any]] = []
    uploads: list[dict[str, Any]] = []
    s3 = _s3()
    bucket = _bucket()

    for spec in sorted(data.pages, key=lambda p: int(p.index)):
        img_ext = _safe_ext(spec.image_ext, ALLOWED_IMAGE_EXT, "jpg")
        aud_ext = _safe_ext(spec.audio_ext, ALLOWED_AUDIO_EXT, "") if spec.audio_ext else ""
        img_key, aud_key = _page_keys(subject_id, spec.index, img_ext, aud_ext)
        img_ct = spec.image_content_type or "image/jpeg"
        aud_ct = spec.audio_content_type or "audio/mpeg"

        img_put = s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": bucket,
                "Key": img_key,
                "ContentType": img_ct,
            },
            ExpiresIn=3600,
        )
        upload: dict[str, Any] = {
            "index": spec.index,
            "image_put_url": img_put,
            "image_content_type": img_ct,
        }
        audio_put = ""
        if aud_key:
            audio_put = s3.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": bucket,
                    "Key": aud_key,
                    "ContentType": aud_ct,
                },
                ExpiresIn=3600,
            )
            upload["audio_put_url"] = audio_put
            upload["audio_content_type"] = aud_ct
        uploads.append(upload)

        pages_out.append(
            {
                "index": spec.index,
                "title": (spec.title or "").strip(),
                "text": (spec.text or "").strip(),
                "image_key": img_key,
                "audio_key": aud_key,
                "image_url": _object_url(img_key),
                "audio_url": _object_url(aud_key) if aud_key else "",
            }
        )

    now = _utcnow_iso()
    db.update_item(
        pk,
        keys.subject_meta_sk(),
        {
            "content_kind": "presentation",
            "presentation_ready": False,
            "page_count": len(pages_out),
            "pages": pages_out,
            "grade_level": data.grade_level or "",
            "uploaded_by": admin_user_id,
            "updated_at": now,
        },
    )
    pub = _public_subject(get_subject(subject_id))
    return {
        **pub,
        "subject_id": subject_id,
        "page_count": len(pages_out),
        "uploads": uploads,
    }


def complete_topic_upload(subject_id: str) -> dict[str, Any]:
    item = get_subject(subject_id)
    if (item.get("category") or "") != "Technology":
        raise TechnologyError("Not a Technology topic")
    updated = db.update_item(
        keys.subject_pk(subject_id),
        keys.subject_meta_sk(),
        {
            "presentation_ready": True,
            "updated_at": _utcnow_iso(),
        },
    )
    return public_technology_topic(updated)


def public_technology_topic(item: dict[str, Any]) -> dict[str, Any]:
    pages = []
    for p in item.get("pages") or []:
        img_key = p.get("image_key") or ""
        aud_key = p.get("audio_key") or ""
        pages.append(
            {
                "index": int(p.get("index") or 0),
                "title": p.get("title") or "",
                "text": p.get("text") or "",
                "image_url": p.get("image_url") or (_object_url(img_key) if img_key else ""),
                "audio_url": p.get("audio_url") or (_object_url(aud_key) if aud_key else ""),
            }
        )
    pages.sort(key=lambda x: x["index"])
    base = _public_subject(item)
    base.update(
        {
            "content_kind": item.get("content_kind") or "presentation",
            "presentation_ready": bool(item.get("presentation_ready")),
            "page_count": int(item.get("page_count") or len(pages)),
            "pages": pages,
        }
    )
    return base


def get_technology_topic(subject_id: str) -> dict[str, Any]:
    item = get_subject(subject_id)
    if (item.get("category") or "") != "Technology":
        raise SubjectNotFound(f"Technology topic '{subject_id}' not found")
    return public_technology_topic(item)


def list_technology_topics() -> list[dict[str, Any]]:
    from app.services.subject_service import list_subjects

    rows = []
    for s in list_subjects():
        if (s.get("category") or "") != "Technology":
            continue
        rows.append(s)
    return rows
