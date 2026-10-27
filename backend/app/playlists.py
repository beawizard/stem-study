"""Facebook Reel playlist membership on a topic.

A topic stores the playlist it belongs to and its place in that playlist.
The Study page groups Science, Technology, and Engineering from those fields.
Reel links are matched on the numeric id because Facebook URLs differ by
host, trailing slash, and query string.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

PLAYLIST_FIELD_NAMES = (
    "playlist_id",
    "playlist_name",
    "playlist_position",
    "playlist_sort",
)
_PLAYLIST_ID_RE = re.compile(r"^\d{5,32}$")
_REEL_ID_RE = re.compile(r"/(?:reel|reels|videos)/(\d+)", re.IGNORECASE)
_HOST_PREFIXES = ("www.", "m.", "web.", "mbasic.")


def blank_playlist_fields() -> dict[str, Any]:
    return {
        "playlist_id": "",
        "playlist_name": "",
        "playlist_position": 0,
        "playlist_sort": 0,
    }


def clean_playlist_id(value: str | None) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return ""
    if not _PLAYLIST_ID_RE.fullmatch(raw):
        raise ValueError("playlist_id must be the numeric Facebook playlist id")
    return raw


def clean_playlist_name(value: str | None) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        raise ValueError("playlist_name cannot contain line breaks or control characters")
    return raw


def normalize_reel_url(url: str) -> str:
    """Stable match key for a Facebook Reel or watch URL.

    fb.watch and facebook.com/share/r/ links are short redirects. The path is
    an opaque code, not the Reel id, and that id is only known after Facebook
    answers an HTTP redirect. Offline, those URLs are keyed as url:<host><path>
    so the same short link matches itself. They do not match a /reel/<id> URL.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    if ":" in host:
        host = host.split(":", 1)[0]
    for prefix in _HOST_PREFIXES:
        if host.startswith(prefix):
            host = host[len(prefix) :]
            break
    path = unquote(parsed.path or "")
    path = re.sub(r"/+", "/", path).rstrip("/")
    reel = _REEL_ID_RE.search(path)
    if reel:
        return f"fb:{reel.group(1)}"
    query = parse_qs(parsed.query, keep_blank_values=False)
    for key in ("v", "video_id", "story_fbid"):
        values = query.get(key) or []
        if values and re.fullmatch(r"\d+", values[0] or ""):
            return f"fb:{values[0]}"
    return f"url:{host}{path.lower()}"


def playlist_update_from_payload(
    existing: dict[str, Any] | None,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Merge playlist fields. None means the caller did not send playlist data."""
    if not any(name in payload for name in PLAYLIST_FIELD_NAMES):
        return None

    def text(key: str) -> str:
        if key in payload and payload[key] is not None:
            return str(payload[key]).strip()
        if existing:
            return str(existing.get(key) or "").strip()
        return ""

    def number(key: str) -> int:
        if key in payload and payload[key] is not None:
            return int(payload[key])
        if existing is not None and existing.get(key) not in (None, ""):
            return int(existing.get(key) or 0)
        return 0

    playlist_id = text("playlist_id")
    playlist_name = text("playlist_name")
    position = number("playlist_position")
    sort = number("playlist_sort")
    if not playlist_id and not playlist_name:
        position = 0
        sort = 0
    return {
        "playlist_id": playlist_id,
        "playlist_name": playlist_name,
        "playlist_position": position,
        "playlist_sort": sort,
    }


def playlist_fields_for_model(existing: dict[str, Any] | None, data: Any) -> dict[str, Any]:
    """Fields to store from a Pydantic model. Omitted inputs keep the current values."""
    fields_set = getattr(data, "model_fields_set", set())
    payload = {
        name: getattr(data, name)
        for name in PLAYLIST_FIELD_NAMES
        if name in fields_set
    }
    merged = playlist_update_from_payload(existing, payload)
    if merged is not None:
        return merged
    if existing:
        return {
            "playlist_id": str(existing.get("playlist_id") or ""),
            "playlist_name": str(existing.get("playlist_name") or ""),
            "playlist_position": int(existing.get("playlist_position") or 0),
            "playlist_sort": int(existing.get("playlist_sort") or 0),
        }
    return blank_playlist_fields()


def public_playlist_fields(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "playlist_id": str(item.get("playlist_id") or ""),
        "playlist_name": str(item.get("playlist_name") or ""),
        "playlist_position": int(item.get("playlist_position") or 0),
        "playlist_sort": int(item.get("playlist_sort") or 0),
    }


def _category_key(value: str) -> str:
    return (value or "").strip().lower()


def _same_playlist(current: dict[str, Any], wanted: dict[str, Any]) -> bool:
    return (
        current["playlist_id"] == wanted["playlist_id"]
        and current["playlist_name"] == wanted["playlist_name"]
        and current["playlist_position"] == wanted["playlist_position"]
        and current["playlist_sort"] == wanted["playlist_sort"]
    )


def _is_assigned(current: dict[str, Any]) -> bool:
    return bool(current["playlist_id"] or current["playlist_name"])


def plan_reel_playlist_backfill(
    catalog: list[dict[str, Any]],
    subjects: list[dict[str, Any]],
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Match catalog reels to topics. Does not write.

    A reel updates every non-deleted topic in the same category whose
    facebook_video_url normalizes to that reel. The first playlist in the
    catalog that lists that reel wins. Later playlists are reported under
    duplicates and do not change the choice. Without force, a topic that
    already has a playlist is left alone. manage_url is ignored.
    """
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for subject in subjects:
        if subject.get("deleted_at"):
            continue
        key = normalize_reel_url(str(subject.get("facebook_video_url") or ""))
        if not key:
            continue
        category = _category_key(str(subject.get("category") or ""))
        by_key.setdefault((category, key), []).append(subject)

    unmatched: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    chosen: dict[str, dict[str, Any]] = {}
    chosen_subject: dict[str, dict[str, Any]] = {}

    sort_by_category: dict[str, int] = {}
    for entry in catalog:
        category = str(entry.get("category") or "").strip()
        sort_by_category[category] = sort_by_category.get(category, 0) + 1
        playlist_sort = sort_by_category[category]
        playlist_id = str(entry.get("playlist_id") or "").strip()
        playlist_name = str(entry.get("name") or "").strip()
        reels = entry.get("reels") or []
        for index, reel in enumerate(reels, start=1):
            title = str((reel or {}).get("title") or "").strip()
            url = str((reel or {}).get("url") or "").strip()
            reel_key = normalize_reel_url(url)
            wanted = {
                "playlist_id": playlist_id,
                "playlist_name": playlist_name,
                "playlist_position": index,
                "playlist_sort": playlist_sort,
            }
            matches = by_key.get((_category_key(category), reel_key), []) if reel_key else []
            if not matches:
                reason = "no_topic"
                other = []
                if reel_key:
                    for (cat, key), rows in by_key.items():
                        if key == reel_key and cat != _category_key(category):
                            other.extend(rows)
                if other:
                    reason = "category_mismatch"
                unmatched.append(
                    {
                        "playlist_name": playlist_name,
                        "category": category,
                        "title": title,
                        "url": url,
                        "reason": reason,
                        "subject_ids": [str(row.get("subject_id") or "") for row in other],
                    }
                )
                continue
            if len(matches) > 1:
                duplicates.append(
                    {
                        "playlist_name": playlist_name,
                        "category": category,
                        "title": title,
                        "url": url,
                        "subject_ids": [str(row.get("subject_id") or "") for row in matches],
                        "reason": "multiple_topics",
                    }
                )
            for subject in matches:
                subject_id = str(subject.get("subject_id") or "")
                kept = chosen.get(subject_id)
                if kept is not None:
                    if not _same_playlist(kept, wanted):
                        duplicates.append(
                            {
                                "playlist_name": playlist_name,
                                "category": category,
                                "title": title,
                                "url": url,
                                "subject_ids": [subject_id],
                                "kept_playlist": kept["playlist_name"],
                                "reason": "playlist_conflict",
                            }
                        )
                    continue
                chosen[subject_id] = wanted
                chosen_subject[subject_id] = subject

    updates: list[dict[str, Any]] = []
    unchanged: list[str] = []
    skipped: list[dict[str, Any]] = []
    for subject_id, wanted in chosen.items():
        current = public_playlist_fields(chosen_subject[subject_id])
        if _same_playlist(current, wanted):
            unchanged.append(subject_id)
            continue
        if not force and _is_assigned(current):
            skipped.append(
                {
                    "subject_id": subject_id,
                    "before": current,
                    "after": dict(wanted),
                }
            )
            continue
        updates.append(
            {
                "subject_id": subject_id,
                "before": current,
                "after": dict(wanted),
                **wanted,
            }
        )

    return {
        "updates": updates,
        "unchanged": unchanged,
        "skipped": skipped,
        "unmatched": unmatched,
        "duplicates": duplicates,
        "force": force,
    }


def apply_reel_playlist_backfill(
    catalog: list[dict[str, Any]],
    *,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Assign playlist fields on existing topics. Safe to run more than once.

    Without force, topics that already have a playlist are skipped.
    """
    from botocore.exceptions import ClientError

    from app import db, keys
    from app.services.subject_service import list_subjects

    plan = plan_reel_playlist_backfill(catalog, list_subjects(), force=force)
    plan["dry_run"] = dry_run
    plan["updated"] = 0
    plan["failures"] = []
    if dry_run or not plan["updates"]:
        return plan

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for row in plan["updates"]:
        try:
            db.update_item(
                keys.subject_pk(row["subject_id"]),
                keys.subject_meta_sk(),
                {
                    "playlist_id": row["playlist_id"],
                    "playlist_name": row["playlist_name"],
                    "playlist_position": int(row["playlist_position"]),
                    "playlist_sort": int(row["playlist_sort"]),
                    "updated_at": now,
                },
                condition="attribute_exists(PK)",
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code") or "ClientError"
            plan["failures"].append({"subject_id": row["subject_id"], "error": code})
            continue
        except Exception as exc:
            plan["failures"].append({"subject_id": row["subject_id"], "error": str(exc)})
            continue
        plan["updated"] += 1
    return plan
