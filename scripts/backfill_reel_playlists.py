#!/usr/bin/env python3
"""Assign topic playlist fields from a Facebook Reels playlist export.

Matches each reel to an existing topic by facebook_video_url. Safe to re-run:
topics that already have the same playlist id, name, and positions are skipped.

Does not read or write manage_url. Do not point --table at a production table
unless you intend to change that environment.

Usage:
  source .venv/bin/activate
  python scripts/backfill_reel_playlists.py --table stem-study-dev --dry-run
  python scripts/backfill_reel_playlists.py --table stem-study-dev
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


def print_report(result: dict) -> None:
    updates = result.get("updates") or []
    unchanged = result.get("unchanged") or []
    unmatched = result.get("unmatched") or []
    duplicates = result.get("duplicates") or []
    print(
        f"dry_run={bool(result.get('dry_run'))} "
        f"updated={result.get('updated', 0)} "
        f"pending={len(updates)} unchanged={len(unchanged)} "
        f"unmatched={len(unmatched)} duplicates={len(duplicates)}"
    )
    if updates:
        print("UPDATES")
        for row in updates:
            print(
                f"  {row['subject_id']}: {row['playlist_name']} "
                f"#{row['playlist_position']} (playlist {row['playlist_id']})"
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
            print(
                f"  [{row.get('category')}] {row.get('title')} — "
                f"{', '.join(row.get('subject_ids') or [])}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", required=True, help="e.g. stem-study-dev")
    parser.add_argument("--region", default="ap-southeast-1")
    parser.add_argument("--playlists", type=Path, default=DEFAULT_PLAYLISTS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    catalog = load_catalog(args.playlists)
    import os

    os.environ["TABLE_NAME"] = args.table
    os.environ["AWS_REGION"] = args.region
    os.environ["AWS_DEFAULT_REGION"] = args.region

    from app import db
    from app.playlists import apply_reel_playlist_backfill

    db.clear_caches()
    result = apply_reel_playlist_backfill(catalog, dry_run=args.dry_run)
    print(
        f"Table={args.table} playlists={len(catalog)} "
        f"reels={sum(len(item.get('reels') or []) for item in catalog)}"
    )
    print_report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
