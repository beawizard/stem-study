"""Home recommended-lesson carousel.

The Melon Manager bot (or an admin) can store an ordered subject list
with PUT /recommendations. Until that list exists, Home shows the two most
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
        "grade_level": str(item.get("grade_level") or "").strip(),
        "visit_count": int(item.get("visit_count") or 0),
        "pick": pick,
    }


def _load_curated_ids() -> list[str]:
    item = db.get_item(keys.recommendations_pk(), keys.recommendations_sk())
    if not item or item.get("deleted_at"):
        return []
    raw = item.get("subject_ids") or []
    if not isinstance(raw, list):
        return []
    ids: list[str] = []
    for sid in raw:
        text = str(sid or "").strip()
        if text and text not in ids:
            ids.append(text)
    return ids


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
    curated_ids = _load_curated_ids()
    if curated_ids:
        curated = [
            _card(by_id[sid], "curated") for sid in curated_ids if sid in by_id
        ]
        if curated:
            return {"source": "curated", "items": curated}
    items, source = _auto_items(subjects, chooser)
    return {"source": source, "items": items}


def set_home_recommendations(
    subject_ids: list[str], *, updated_by: str = ""
) -> dict[str, Any]:
    """Replace the curated carousel. An empty list restores automatic picks."""
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
            "updated_at": now,
            "updated_by": updated_by or "",
            "deleted_at": "",
        }
    )
    return list_home_recommendations()
