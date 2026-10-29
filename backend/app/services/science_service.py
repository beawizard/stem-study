"""Science topics — optional summary/cover, interactive HTML, explainer video URL."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import boto3

from app import db, keys
from app.playlists import playlist_fields_for_model
from app.services.subject_service import (
    ConflictError,
    SubjectNotFound,
    create_subject,
    get_subject,
    reject_replace_if_name_differs,
    display_topic_name,
    is_lesson_topic,
    resolve_topic_upload,
    update_subject,
    _public_subject,
)
from app.validation import (
    MAX_DESC_LEN,
    STEM_CATEGORIES,
    SubjectCreate,
    SubjectUpdate,
    ScienceTopicCreate,
    remove_bom,
)

ALLOWED_IMAGE_EXT = {"jpg", "jpeg", "png", "webp", "gif", "bmp"}
ALLOWED_HTML_EXT = {"html", "htm"}
_S3_PREFIX = {
    "Science": "science",
    "Technology": "technology",
    "Engineering": "engineering",
    "Mathematics": "mathematics",
}


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


def _s3_prefix(category: str) -> str:
    return _S3_PREFIX.get(category, "science")


def _cover_key(category: str, subject_id: str, image_ext: str) -> str:
    return f"{_s3_prefix(category)}/{subject_id}/cover.{image_ext}"


def _html_key(category: str, subject_id: str) -> str:
    return f"{_s3_prefix(category)}/{subject_id}/interactive.html"


def _kept_text(incoming: str | None, existing: dict[str, Any] | None, key: str) -> str:
    """Use incoming text. None on replace keeps the value already stored."""
    if existing and incoming is None:
        return str(existing.get(key) or "")
    return (incoming or "").strip()


def start_topic_upload(data: ScienceTopicCreate, *, admin_user_id: str) -> dict[str, Any]:
    """Create/replace a STEM lesson topic and optionally return presigned PUTs."""
    category = (data.category or "Science").strip() or "Science"
    if category not in STEM_CATEGORIES:
        raise ScienceError(f"category must be one of: {', '.join(STEM_CATEGORIES)}")
    submitted = remove_bom(data.topic).strip()
    # Technology presentations are not lesson rows. Every other category must
    # not reuse or convert a quiz subject the upload form does not list.
    subject_id, existing = resolve_topic_upload(
        category,
        submitted,
        lessons_only=category != "Technology",
    )
    pk = keys.subject_pk(subject_id)
    live = bool(existing and not existing.get("deleted_at"))
    if live and not data.replace:
        raise ConflictError(
            f"Topic '{submitted}' already exists. Enable replace to overwrite."
        )
    if live and data.replace:
        reject_replace_if_name_differs(existing, submitted)
    topic = display_topic_name(existing if live else None, submitted)

    # Omitted or null grade, summary, and video links keep the saved values
    # when this upload replaces a topic. A value the caller actually sent is stored.
    # Explicit "" clears grade, summary, and description.
    summary = _kept_text(data.summary, existing, "summary")
    video = _kept_text(data.explainer_video_url, existing, "explainer_video_url")
    facebook = _kept_text(data.facebook_video_url, existing, "facebook_video_url")
    grade_level = _kept_text(data.grade_level, existing, "grade_level")
    if existing and data.summary is None:
        desc = str(existing.get("description") or "")
    else:
        desc = summary[:MAX_DESC_LEN]

    if existing:
        update_subject(
            subject_id,
            SubjectUpdate(
                category=category,
                topic=topic,
                description=desc,
                grade_level=grade_level,
            ),
        )
    else:
        create_subject(
            SubjectCreate(
                category=category,
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

    has_html = bool((data.html_ext or "").strip())
    html_key = (existing or {}).get("html_key") or ""
    html_url = (existing or {}).get("html_url") or (existing or {}).get(
        "interactive_html_url"
    ) or ""
    html_put_url = ""
    html_ct = (data.html_content_type or "").strip() or "text/html"

    s3 = None
    bucket = ""
    if has_image or has_html:
        s3 = _s3()
        bucket = _bucket()

    if has_image:
        img_ext = _safe_ext(data.image_ext, ALLOWED_IMAGE_EXT, "jpg")
        image_key = _cover_key(category, subject_id, img_ext)
        image_url = _object_url(image_key)
        image_put_url = s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": bucket,
                "Key": image_key,
                "ContentType": image_ct,
            },
            ExpiresIn=3600,
        )

    if has_html:
        html_ext = _safe_ext(data.html_ext, ALLOWED_HTML_EXT, "html")
        if html_ext not in ALLOWED_HTML_EXT:
            raise ScienceError("Interactive HTML must be a .html file")
        html_key = _html_key(category, subject_id)
        html_url = _object_url(html_key)
        html_put_url = s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": bucket,
                "Key": html_key,
                "ContentType": html_ct,
            },
            ExpiresIn=3600,
        )

    now = _utcnow_iso()
    db.update_item(
        pk,
        keys.subject_meta_sk(),
        {
            "content_kind": "lesson",
            "science_ready": False,
            "summary": summary,
            "explainer_video_url": video,
            "facebook_video_url": facebook,
            "image_key": image_key,
            "image_url": image_url,
            "html_key": html_key,
            "html_url": html_url,
            "grade_level": grade_level,
            "uploaded_by": admin_user_id,
            "updated_at": now,
            **playlist_fields_for_model(existing, data),
        },
    )
    pub = public_science_topic(get_subject(subject_id))
    return {
        **pub,
        "subject_id": subject_id,
        "image_put_url": image_put_url,
        "image_content_type": image_ct if image_put_url else "",
        "html_put_url": html_put_url,
        "html_content_type": html_ct if html_put_url else "",
    }


def complete_topic_upload(subject_id: str) -> dict[str, Any]:
    item = get_subject(subject_id)
    kind = (item.get("content_kind") or "").strip()
    if kind not in ("science", "lesson"):
        raise ScienceError("Not an uploaded lesson topic")
    if (item.get("category") or "") not in STEM_CATEGORIES:
        raise ScienceError("Not an uploaded lesson topic")
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
    html_key = item.get("html_key") or ""
    html_url = (
        item.get("html_url")
        or item.get("interactive_html_url")
        or (_object_url(html_key) if html_key else "")
    )
    base = _public_subject(item)
    base.update(
        {
            "content_kind": item.get("content_kind") or "lesson",
            "science_ready": bool(item.get("science_ready")),
            "summary": (
                item["summary"]
                if "summary" in item
                else (item.get("description") or "")
            )
            or "",
            "explainer_video_url": item.get("explainer_video_url") or "",
            "facebook_video_url": item.get("facebook_video_url") or "",
            "image_url": item.get("image_url") or (_object_url(img_key) if img_key else ""),
            "interactive_html_url": html_url,
        }
    )
    return base


def get_science_topic(subject_id: str) -> dict[str, Any]:
    item = get_subject(subject_id)
    if (item.get("category") or "") not in STEM_CATEGORIES:
        raise SubjectNotFound(f"Lesson topic '{subject_id}' not found")
    if not is_lesson_topic(item):
        raise SubjectNotFound(f"Lesson topic '{subject_id}' not found")
    return public_science_topic(item)


def list_science_topics() -> list[dict[str, Any]]:
    from app.services.subject_service import list_subjects

    rows = []
    for s in list_subjects():
        if (s.get("category") or "") not in STEM_CATEGORIES:
            continue
        kind = (s.get("content_kind") or "").strip()
        if kind in ("science", "lesson") or s.get("science_ready"):
            rows.append(s)
    return rows
