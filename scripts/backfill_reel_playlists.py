#!/usr/bin/env python3
"""Assign topic playlist fields from a Facebook Reels playlist export.

Matches each reel to an existing topic by facebook_video_url. The first
playlist in the file that lists a reel wins. By default only topics with no
playlist are updated, so a later manual edit is kept. Pass --force to
overwrite. Safe to re-run.

Does not read or write manage_url. Do not point --table at a production table
unless you intend to change that environment.

Usage:
  source .venv/bin/activate
  python scripts/backfill_reel_playlists.py --table stem-study-dev --dry-run
  python scripts/backfill_reel_playlists.py --table stem-study-dev
  python scripts/backfill_reel_playlists.py --table stem-study-dev --force
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

DEFAULT_PLAYLISTS = ROOT / "scripts" / "data" / "facebook_reels_playlists.json"


def load_catalog(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit(f"{path} must be a JSON list of playlists")
    return data


def _playlist_label(fields: dict | None) -> str:
    fields = fields or {}
    name = fields.get("playlist_name") or "(none)"
    playlist_id = fields.get("playlist_id") or ""
    return (
        f"name={name!r} id={playlist_id} "
        f"position={fields.get('playlist_position', 0)} "
        f"sort={fields.get('playlist_sort', 0)}"
    )


def print_report(result: dict) -> None:
    updates = result.get("updates") or []
    unchanged = result.get("unchanged") or []
    skipped = result.get("skipped") or []
    unmatched = result.get("unmatched") or []
    duplicates = result.get("duplicates") or []
    failures = result.get("failures") or []
    print(
        f"dry_run={bool(result.get('dry_run'))} force={bool(result.get('force'))} "
        f"updated={result.get('updated', 0)} "
        f"pending={len(updates)} unchanged={len(unchanged)} "
        f"skipped={len(skipped)} unmatched={len(unmatched)} "
        f"duplicates={len(duplicates)} failures={len(failures)}"
    )
    if updates:
        print("UPDATES")
        for row in updates:
            print(
                f"  {row['subject_id']}: {_playlist_label(row.get('before'))} "
                f"-> {_playlist_label(row.get('after') or row)}"
            )
    if skipped:
        print("SKIPPED")
        for row in skipped:
            print(
                f"  {row['subject_id']}: kept {_playlist_label(row.get('before'))} "
                f"(catalog {_playlist_label(row.get('after'))})"
            )
    if unmatched:
        print("UNMATCHED")
        for row in unmatched:
            subjects = ",".join(row.get("subject_ids") or [])
            extra = f" subjects={subjects}" if subjects else ""
            print(
                f"  [{row.get('category')}] {row.get('playlist_name')} — "
                f"{row.get('title')} — {row.get('url')} — {row.get('reason')}{extra}"
            )
    if duplicates:
        print("DUPLICATES")
        for row in duplicates:
            kept = row.get("kept_playlist")
            extra = f" kept={kept}" if kept else ""
            print(
                f"  [{row.get('category')}] {row.get('playlist_name')} — "
                f"{row.get('title')} — {', '.join(row.get('subject_ids') or [])} — "
                f"{row.get('reason') or 'duplicate'}{extra}"
            )
    if failures:
        print("FAILURES")
        for row in failures:
            print(f"  {row.get('subject_id')}: {row.get('error')}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", required=True, help="e.g. stem-study-dev")
    parser.add_argument("--region", default="ap-southeast-1")
    parser.add_argument("--playlists", type=Path, default=DEFAULT_PLAYLISTS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite topics that already have a playlist",
    )
    args = parser.parse_args()

    catalog = load_catalog(args.playlists)
    import os

    os.environ["TABLE_NAME"] = args.table
    os.environ["AWS_REGION"] = args.region
    os.environ["AWS_DEFAULT_REGION"] = args.region

    from app import db
    from app.playlists import apply_reel_playlist_backfill

    db.clear_caches()
    result = apply_reel_playlist_backfill(
        catalog, dry_run=args.dry_run, force=args.force
    )
    print(
        f"Table={args.table} playlists={len(catalog)} "
        f"reels={sum(len(item.get('reels') or []) for item in catalog)}"
    )
    print_report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
