#!/usr/bin/env python3
"""Backfill SUBJECT grade_level from topic name (Level N → Kindergarten / Grade N-1).

Mapping (from GradeLevelAttribute table):
  … Level 1 → Kindergarten
  … Level 2 → Grade 1
  … Level 3 → Grade 2
  … Level 4 → Grade 3
  … Level 5 → Grade 4
  … Level 6 → Grade 5

Usage:
  source .venv/bin/activate
  python scripts/backfill_subject_grade_levels.py --table stem-study-dev
  python scripts/backfill_subject_grade_levels.py --table stem-study-prod
  python scripts/backfill_subject_grade_levels.py --table stem-study-dev --dry-run
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

LEVEL_TO_GRADE = {
    1: "Kindergarten",
    2: "Grade 1",
    3: "Grade 2",
    4: "Grade 3",
    5: "Grade 4",
    6: "Grade 5",
    7: "Grade 6",
    8: "Grade 7",
    9: "Grade 8",
    10: "Grade 9",
    11: "Grade 10",
    12: "Grade 11",
    13: "Grade 12",
}

_LEVEL_RE = re.compile(r"Level\s*(\d+)\s*$", re.IGNORECASE)


def infer_grade_level(topic: str) -> str | None:
    m = _LEVEL_RE.search((topic or "").strip())
    if not m:
        return None
    n = int(m.group(1))
    return LEVEL_TO_GRADE.get(n)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--table", required=True, help="e.g. stem-study-dev or stem-study-prod")
    p.add_argument("--region", default="ap-southeast-1")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing grade_level values",
    )
    args = p.parse_args()

    table = boto3.resource("dynamodb", region_name=args.region).Table(args.table)
    resp = table.query(
        IndexName="GSI1",
        KeyConditionExpression=Key("GSI1PK").eq("ENTITY#SUBJECT"),
    )
    items = list(resp.get("Items") or [])
    while resp.get("LastEvaluatedKey"):
        resp = table.query(
            IndexName="GSI1",
            KeyConditionExpression=Key("GSI1PK").eq("ENTITY#SUBJECT"),
            ExclusiveStartKey=resp["LastEvaluatedKey"],
        )
        items.extend(resp.get("Items") or [])

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    updated = 0
    skipped = 0
    unknown = 0

    print(f"Table={args.table} subjects={len(items)} dry_run={args.dry_run}")
    for item in items:
        if item.get("deleted_at"):
            continue
        topic = (item.get("topic") or item.get("name") or "").strip()
        sid = item.get("subject_id") or ""
        existing = (item.get("grade_level") or "").strip()
        inferred = infer_grade_level(topic)
        if not inferred:
            unknown += 1
            print(f"  ? no Level N in topic: {topic!r} ({sid})")
            continue
        if existing and not args.force:
            if existing == inferred:
                skipped += 1
                continue
            print(f"  ~ keep existing {existing!r} (inferred {inferred!r}): {topic}")
            skipped += 1
            continue
        print(f"  → {inferred}: {topic} ({sid})")
        if not args.dry_run:
            table.update_item(
                Key={"PK": item["PK"], "SK": item["SK"]},
                UpdateExpression="SET grade_level = :g, updated_at = :u",
                ExpressionAttributeValues={":g": inferred, ":u": now},
            )
        updated += 1

    print(f"Done. updated={updated} skipped={skipped} unknown={unknown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
