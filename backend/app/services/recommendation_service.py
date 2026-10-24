"""Home recommended-lesson carousel.

The Melon Manager bot (or an admin) can store an ordered subject list
with PUT /recommendations, and an optional PNG or JPEG thumbnail for each id.
Until that list exists, Home shows the two most
visited topics in each STEM category. A category with no visits yet contributes
two topics chosen at random, so a full catalog yields eight cards.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any

from app import db, keys
from app.services.subject_service import _subject_category, _subject_topic

STEM_ORDER = ("Science", "Technology", "Engineering", "Mathematics")
PER_CATEGORY = 2


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _raw_subjects() -> list[dict[str, Any]]:
    items = db.query_gsi1(keys.ENTITY_SUBJECT)
    return [
        item
        for item in items
        if item.get("entity_type") == "SUBJECT" and not item.get("deleted_at")
    ]


def _eligible(item: dict[str, Any]) -> bool:
    """Topics the Study page can open from a carousel tap.

    Science and Engineering need a finished lesson. Technology needs a
    finished presentation or lesson. Mathematics topics are quiz sets.
    """
    category = _subject_category(item)
    if category not in STEM_ORDER:
        return False
    if category == "Mathematics":
        return True
    if category == "Technology":
        return bool(item.get("presentation_ready") or item.get("science_ready"))
    return bool(item.get("science_ready"))


def _html_url(item: dict[str, Any]) -> str:
    """Interactive lesson page, used as the carousel picture when there is no cover."""
    url = str(item.get("html_url") or item.get("interactive_html_url") or "").strip()
    if url:
        return url
    key = str(item.get("html_key") or "").strip().lstrip("/")
    if not key:
        return ""
    try:
        from app.services.science_service import _object_url

        return _object_url(key)
    except Exception:
        return ""


def _image_url(item: dict[str, Any]) -> str:
    """Cover image, or the first Technology slide when there is no cover."""
    url = str(item.get("image_url") or "").strip()
    if url:
        return url
    pages = item.get("pages") or []
    if not isinstance(pages, list):
        return ""
    ordered = sorted(pages, key=lambda page: int((page or {}).get("index") or 0))
    for page in ordered:
        if not isinstance(page, dict):
            continue
        slide = str(page.get("image_url") or "").strip()
        if slide:
            return slide
    return ""


def _card(item: dict[str, Any], pick: str) -> dict[str, Any]:
    return {
        "subject_id": item.get("subject_id") or "",
        "category": _subject_category(item),
        "topic": _subject_topic(item),
        "image_url": _image_url(item),
        "interactive_html_url": _html_url(item),
        "grade_level": str(item.get("grade_level") or "").strip(),
        "visit_count": int(item.get("visit_count") or 0),
        "pick": pick,
    }


def _load_curated() -> tuple[list[str], dict[str, str]]:
    item = db.get_item(keys.recommendations_pk(), keys.recommendations_sk())
    if not item or item.get("deleted_at"):
        return [], {}
    raw = item.get("subject_ids") or []
    ids: list[str] = []
    if isinstance(raw, list):
        for sid in raw:
            text = str(sid or "").strip()
            if text and text not in ids:
                ids.append(text)
    thumbs: dict[str, str] = {}
    raw_thumbs = item.get("thumbnails") or {}
    if isinstance(raw_thumbs, dict):
        for sid, url in raw_thumbs.items():
            text = str(sid or "").strip()
            link = str(url or "").strip()
            if text and link:
                thumbs[text] = link
    return ids, thumbs


def _pick_category(
    pool: list[dict[str, Any]], rng: random.Random
) -> tuple[list[dict[str, Any]], str]:
    if not pool:
        return [], "empty"
    if any(int(item.get("visit_count") or 0) > 0 for item in pool):
        ranked = sorted(
            pool,
            key=lambda item: (
                -int(item.get("visit_count") or 0),
                str(item.get("subject_id") or ""),
            ),
        )
        return ranked[:PER_CATEGORY], "visited"
    count = min(PER_CATEGORY, len(pool))
    return rng.sample(list(pool), count), "random"


def _auto_items(
    subjects: list[dict[str, Any]], rng: random.Random
) -> tuple[list[dict[str, Any]], str]:
    items: list[dict[str, Any]] = []
    modes: set[str] = set()
    eligible = [item for item in subjects if _eligible(item)]
    for category in STEM_ORDER:
        pool = [item for item in eligible if _subject_category(item) == category]
        picked, how = _pick_category(pool, rng)
        if how in ("visited", "random"):
            modes.add(how)
        for item in picked:
            items.append(_card(item, how))
    if not items:
        return [], "empty"
    if modes == {"visited"}:
        return items, "visited"
    if modes == {"random"}:
        return items, "random"
    return items, "mixed"


def list_home_recommendations(rng: random.Random | None = None) -> dict[str, Any]:
    """Cards for the Home carousel. Curated ids win over the automatic pick."""
    chooser = rng or random.Random()
    subjects = _raw_subjects()
    by_id = {str(item.get("subject_id") or ""): item for item in subjects}
    curated_ids, thumbnails = _load_curated()
    if curated_ids:
        curated = []
        for sid in curated_ids:
            if sid not in by_id:
                continue
            card = _card(by_id[sid], "curated")
            override = thumbnails.get(sid, "")
            if override:
                card["image_url"] = override
            curated.append(card)
        if curated:
            return {"source": "curated", "items": curated}
    items, source = _auto_items(subjects, chooser)
    return {"source": source, "items": items}


_THUMB_PREFIX = {
    "Science": "science",
    "Technology": "technology",
    "Engineering": "engineering",
    "Mathematics": "mathematics",
}


def _store_thumbnail(subject: dict[str, Any], raw_image: str) -> str:
    """Save a manager thumbnail under the lesson prefix and return its URL."""
    from app.services.science_service import ScienceError, _bucket, _object_url, _s3
    from app.validation import decode_thumbnail_image

    data, ext, content_type = decode_thumbnail_image(raw_image)
    category = _subject_category(subject)
    prefix = _THUMB_PREFIX.get(category)
    if not prefix:
        raise ValueError(f"No thumbnail folder for category '{category}'")
    subject_id = str(subject.get("subject_id") or "").strip()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    key = f"{prefix}/{subject_id}/carousel/{stamp}.{ext}"
    try:
        _s3().put_object(
            Bucket=_bucket(),
            Key=key,
            Body=data,
            ContentType=content_type,
            CacheControl="public, max-age=31536000, immutable",
        )
    except ScienceError as exc:
        raise ValueError(str(exc)) from exc
    return _object_url(key)


def set_home_recommendations(
    subject_ids: list[str],
    *,
    thumbnails: dict[str, str] | None = None,
    updated_by: str = "",
) -> dict[str, Any]:
    """Replace the curated carousel. An empty list restores automatic picks.

    thumbnails maps a subject id to a base64 PNG or JPEG. The file is stored
    beside that lesson and the card uses its public URL. A missing id keeps
    the thumbnail already saved for it, then the lesson cover, then the
    interactive lesson.
    """
    subjects = _raw_subjects()
    by_id = {str(item.get("subject_id") or ""): item for item in subjects}
    clean: list[str] = []
    for raw in subject_ids:
        sid = str(raw or "").strip()
        if not sid or sid in clean:
            continue
        if sid not in by_id:
            raise ValueError(f"Unknown topic '{sid}'")
        clean.append(sid)
    _, previous = _load_curated()
    clean_thumbs: dict[str, str] = {}
    for sid in clean:
        if sid in previous:
            clean_thumbs[sid] = previous[sid]
    for raw_id, raw_image in (thumbnails or {}).items():
        sid = str(raw_id or "").strip()
        image = str(raw_image or "").strip()
        if not sid or not image:
            continue
        if sid not in by_id or sid not in clean:
            raise ValueError(f"Thumbnail is not in subject_ids: '{sid}'")
        clean_thumbs[sid] = _store_thumbnail(by_id[sid], image)

    pk = keys.recommendations_pk()
    sk = keys.recommendations_sk()
    if not clean:
        existing = db.get_item(pk, sk)
        if existing:
            db.delete_item(pk, sk)
        return list_home_recommendations()

    now = _utcnow_iso()
    db.put_item(
        {
            "PK": pk,
            "SK": sk,
            "entity_type": "RECOMMENDATIONS",
            "subject_ids": clean,
            "thumbnails": clean_thumbs,
            "updated_at": now,
            "updated_by": updated_by or "",
            "deleted_at": "",
        }
    )
    return list_home_recommendations()
