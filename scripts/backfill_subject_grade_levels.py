#!/usr/bin/env python3
"""Backfill SUBJECT grade_level from Operation + App Level (GradeLevelAttribute table).

Canonical mapping
-----------------
Addition & Subtraction:
  Level 1, Level 2 → Kindergarten
  Level 3          → Grade 1
  Level 4          → Grade 2
  Level 5          → Grade 3
  Level 6          → Grade 4

Multiplication:
  Level 1          → Grade 1

Usage:
  source .venv/bin/activate
  python scripts/backfill_subject_grade_levels.py --table stem-study-dev --force
  python scripts/backfill_subject_grade_levels.py --table stem-study-prod --force
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

_LEVEL_RE = re.compile(r"Level\s*(\d+)\s*$", re.IGNORECASE)

# Operation family → App Level → Grade Level (from GradeLevelAttribute.png)
ADD_SUB_LEVEL_TO_GRADE = {
    1: "Kindergarten",
    2: "Kindergarten",
    3: "Grade 1",
    4: "Grade 2",
    5: "Grade 3",
    6: "Grade 4",
}

MULT_LEVEL_TO_GRADE = {
    1: "Grade 1",
}


def _operation_family(topic: str) -> str | None:
    t = (topic or "").lower()
    if "multiplication" in t or "multiply" in t:
        return "multiplication"
    if "addition" in t or "add" in t:
        return "addition"
    if "subtraction" in t or "subtract" in t:
        return "subtraction"
    return None


def infer_grade_level(topic: str) -> str | None:
    """Map topic title to grade_level using Operation + App Level columns."""
    topic = (topic or "").strip()
    m = _LEVEL_RE.search(topic)
    if not m:
        return None
    app_level = int(m.group(1))
    family = _operation_family(topic)
    if family in ("addition", "subtraction"):
        return ADD_SUB_LEVEL_TO_GRADE.get(app_level)
    if family == "multiplication":
        return MULT_LEVEL_TO_GRADE.get(app_level)
    return None


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

    print(f"Table={args.table} subjects={len(items)} dry_run={args.dry_run} force={args.force}")
    for item in items:
        if item.get("deleted_at"):
            continue
        topic = (item.get("topic") or item.get("name") or "").strip()
        sid = item.get("subject_id") or ""
        existing = (item.get("grade_level") or "").strip()
        inferred = infer_grade_level(topic)
        if not inferred:
            unknown += 1
            print(f"  ? no mapping for topic: {topic!r} ({sid})")
            continue
        if existing == inferred and not args.force:
            skipped += 1
            continue
        if existing and existing != inferred and not args.force:
            print(f"  ~ keep existing {existing!r} (table says {inferred!r}): {topic}")
            skipped += 1
            continue
        if existing and existing != inferred:
            print(f"  ✎ {existing!r} → {inferred!r}: {topic} ({sid})")
        else:
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
