#!/usr/bin/env python3
"""Migrate DynamoDB content + Cognito users from dev → prod.

Usage:
  source .venv/bin/activate
  python scripts/migrate_dev_to_prod.py \\
    --source-table stem-study-dev \\
    --dest-table stem-study-prod \\
    --source-pool ap-southeast-1_XXXX \\
    --dest-pool ap-southeast-1_YYYY \\
    --region ap-southeast-1

What is copied
--------------
DynamoDB (all items whose PK matches):
  - SUBJECT#*   Math (and other) topics, levels, questions
  - SCHOOL#*    school catalog
  - USER#*      profiles, progress, sessions, tasks, payments, FB claims
  - Any other PK prefixes present in source (full app state)

Cognito:
  - All users (email, attributes, enabled, email_verified)
  - Group memberships (e.g. admin)
  - Passwords CANNOT be exported; users get a one-time temporary password
    written to scripts/.migrate-temp-passwords.json (gitignored)

Idempotent: re-running overwrites DynamoDB items and skips existing Cognito users
unless --force-cognito is set.
"""

from __future__ import annotations

import argparse
import json
import secrets
import string
import sys
import time
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parent.parent
PASSWORD_FILE = Path(__file__).resolve().parent / ".migrate-temp-passwords.json"


def _client(service: str, region: str):
    return boto3.client(service, region_name=region)


def _resource(service: str, region: str):
    return boto3.resource(service, region_name=region)


def scan_all(table_name: str, region: str) -> list[dict[str, Any]]:
    table = _resource("dynamodb", region).Table(table_name)
    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {}
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return items


def should_migrate_item(
    item: dict[str, Any],
    *,
    content_only: bool = False,
    subjects_only: bool = False,
) -> bool:
    pk = str(item.get("PK") or "")
    if subjects_only:
        # Category / topic / level / question catalog only (no learners, no schools)
        return pk.startswith("SUBJECT#")
    if content_only:
        return pk.startswith("SUBJECT#") or pk.startswith("SCHOOL#")
    # Full app state for learners + content
    return True


def batch_write(table_name: str, region: str, items: list[dict[str, Any]]) -> int:
    table = _resource("dynamodb", region).Table(table_name)
    written = 0
    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=item)
            written += 1
    return written


def list_cognito_users(pool_id: str, region: str) -> list[dict[str, Any]]:
    client = _client("cognito-idp", region)
    users: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"UserPoolId": pool_id, "Limit": 60}
    while True:
        resp = client.list_users(**kwargs)
        users.extend(resp.get("Users", []))
        token = resp.get("PaginationToken")
        if not token:
            break
        kwargs["PaginationToken"] = token
    return users


def list_user_groups(pool_id: str, username: str, region: str) -> list[str]:
    client = _client("cognito-idp", region)
    groups: list[str] = []
    kwargs: dict[str, Any] = {
        "UserPoolId": pool_id,
        "Username": username,
        "Limit": 60,
    }
    while True:
        try:
            resp = client.admin_list_groups_for_user(**kwargs)
        except ClientError:
            return groups
        groups.extend(g["GroupName"] for g in resp.get("Groups", []))
        token = resp.get("NextToken")
        if not token:
            break
        kwargs["NextToken"] = token
    return groups


def gen_temp_password(length: int = 16) -> str:
    # Cognito policy: upper, lower, digit, symbol
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in pwd)
            and any(c.isupper() for c in pwd)
            and any(c.isdigit() for c in pwd)
            and any(c in "!@#$%^&*" for c in pwd)
        ):
            return pwd


def migrate_cognito(
    *,
    source_pool: str,
    dest_pool: str,
    region: str,
    force: bool,
) -> dict[str, Any]:
    client = _client("cognito-idp", region)
    users = list_cognito_users(source_pool, region)
    report: dict[str, Any] = {"created": [], "skipped": [], "errors": [], "passwords": {}}

    for user in users:
        src_username = user["Username"]
        attrs = {a["Name"]: a["Value"] for a in user.get("Attributes", [])}
        email = (attrs.get("email") or "").strip().lower()
        # Email-as-username pools require Username to be the email address
        dest_username = email or src_username
        if "@" not in dest_username:
            report["errors"].append(
                {
                    "user": src_username,
                    "error": "No email attribute; cannot create in email-alias pool",
                }
            )
            continue
        enabled = user.get("Enabled", True)
        status = user.get("UserStatus")

        # Check exists (by email username)
        try:
            client.admin_get_user(UserPoolId=dest_pool, Username=dest_username)
            exists = True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "UserNotFoundException":
                exists = False
            else:
                report["errors"].append({"user": dest_username, "error": str(exc)})
                continue

        if exists and not force:
            report["skipped"].append(dest_username)
            for g in list_user_groups(source_pool, src_username, region):
                try:
                    client.admin_add_user_to_group(
                        UserPoolId=dest_pool, Username=dest_username, GroupName=g
                    )
                except ClientError:
                    pass
            continue

        attr_list = []
        for name, value in attrs.items():
            if name in ("sub",):
                continue
            attr_list.append({"Name": name, "Value": value})
        # Ensure email attribute present
        if not any(a["Name"] == "email" for a in attr_list):
            attr_list.append({"Name": "email", "Value": dest_username})

        temp_pwd = gen_temp_password()
        try:
            if not exists:
                client.admin_create_user(
                    UserPoolId=dest_pool,
                    Username=dest_username,
                    UserAttributes=attr_list,
                    MessageAction="SUPPRESS",
                    TemporaryPassword=temp_pwd,
                )
            client.admin_set_user_password(
                UserPoolId=dest_pool,
                Username=dest_username,
                Password=temp_pwd,
                Permanent=True,
            )
            if not enabled:
                client.admin_disable_user(
                    UserPoolId=dest_pool, Username=dest_username
                )
            if status == "CONFIRMED" or attrs.get("email_verified") == "true":
                client.admin_update_user_attributes(
                    UserPoolId=dest_pool,
                    Username=dest_username,
                    UserAttributes=[{"Name": "email_verified", "Value": "true"}],
                )
            for g in list_user_groups(source_pool, src_username, region):
                try:
                    client.admin_add_user_to_group(
                        UserPoolId=dest_pool, Username=dest_username, GroupName=g
                    )
                except ClientError as exc:
                    report["errors"].append(
                        {"user": dest_username, "group": g, "error": str(exc)}
                    )
            report["created"].append(dest_username)
            report["passwords"][dest_username] = temp_pwd
            report.setdefault("sub_map", {})[attrs.get("sub") or src_username] = (
                dest_username
            )
        except ClientError as exc:
            report["errors"].append({"user": dest_username, "error": str(exc)})

    # Build old_sub → new_sub map for DynamoDB USER# rewrite
    sub_map: dict[str, str] = {}
    for user in users:
        src_username = user["Username"]
        attrs = {a["Name"]: a["Value"] for a in user.get("Attributes", [])}
        old_sub = attrs.get("sub") or src_username
        email = (attrs.get("email") or "").strip().lower()
        if not email:
            continue
        try:
            dest = client.admin_get_user(UserPoolId=dest_pool, Username=email)
        except ClientError:
            continue
        new_attrs = {a["Name"]: a["Value"] for a in dest.get("UserAttributes", [])}
        new_sub = new_attrs.get("sub")
        if new_sub and old_sub:
            sub_map[old_sub] = new_sub
    report["sub_map"] = sub_map

    if report["passwords"]:
        PASSWORD_FILE.write_text(
            json.dumps(
                {
                    "warning": "TEMPORARY passwords after Cognito migration. Rotate/change ASAP. Do not commit.",
                    "dest_pool": dest_pool,
                    "users": report["passwords"],
                    "sub_map": sub_map,
                },
                indent=2,
            )
            + "\n"
        )
        print(f"Wrote temporary passwords → {PASSWORD_FILE} (gitignored)")
    return report


def remap_user_items(
    table_name: str, region: str, sub_map: dict[str, str]
) -> tuple[int, int]:
    """Rewrite USER#<old_sub>… → USER#<new_sub>… so JWT sub matches profiles."""
    if not sub_map:
        return 0, 0
    table = _resource("dynamodb", region).Table(table_name)
    written = 0
    deleted = 0
    for old_sub, new_sub in sub_map.items():
        if old_sub == new_sub:
            continue
        old_pk = f"USER#{old_sub}"
        new_pk = f"USER#{new_sub}"
        # Query all items under old PK
        items: list[dict[str, Any]] = []
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": "PK = :pk",
            "ExpressionAttributeValues": {":pk": old_pk},
        }
        while True:
            resp = table.query(**kwargs)
            items.extend(resp.get("Items", []))
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek
        for item in items:
            new_item = dict(item)
            new_item["PK"] = new_pk
            if new_item.get("user_id") == old_sub:
                new_item["user_id"] = new_sub
            table.put_item(Item=new_item)
            written += 1
            table.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
            deleted += 1
    return written, deleted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-table", default="stem-study-dev")
    parser.add_argument("--dest-table", default="stem-study-prod")
    parser.add_argument(
        "--source-pool",
        default="",
        help="Required unless --skip-cognito",
    )
    parser.add_argument(
        "--dest-pool",
        default="",
        help="Required unless --skip-cognito",
    )
    parser.add_argument("--region", default="ap-southeast-1")
    parser.add_argument(
        "--content-only",
        action="store_true",
        help="Only SUBJECT# and SCHOOL# (skip USER# progress)",
    )
    parser.add_argument(
        "--subjects-only",
        action="store_true",
        help="Only SUBJECT# catalog (topics/levels/questions). No schools, no learners.",
    )
    parser.add_argument(
        "--skip-dynamodb",
        action="store_true",
    )
    parser.add_argument(
        "--skip-cognito",
        action="store_true",
    )
    parser.add_argument(
        "--force-cognito",
        action="store_true",
        help="Reset password for users that already exist in dest pool",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.skip_cognito and (not args.source_pool or not args.dest_pool):
        parser.error("--source-pool and --dest-pool are required unless --skip-cognito")

    direction = f"{args.source_table} → {args.dest_table}"
    print("=== DynamoDB / Cognito migration ===")
    print(f"DynamoDB: {direction}")
    if args.skip_cognito:
        print("Cognito:  skipped")
    else:
        print(f"Cognito:  {args.source_pool} → {args.dest_pool}")
    print(f"Region:   {args.region}")
    if args.subjects_only:
        print("Filter:   SUBJECT# only (categories/topics/levels/questions)")
    elif args.content_only:
        print("Filter:   SUBJECT# + SCHOOL#")

    if not args.skip_dynamodb:
        print("\nScanning source table…")
        items = scan_all(args.source_table, args.region)
        selected = [
            i
            for i in items
            if should_migrate_item(
                i,
                content_only=args.content_only,
                subjects_only=args.subjects_only,
            )
        ]
        # Stats
        prefixes: dict[str, int] = {}
        entity_types: dict[str, int] = {}
        for i in selected:
            p = str(i.get("PK") or "").split("#")[0]
            prefixes[p] = prefixes.get(p, 0) + 1
            et = str(i.get("entity_type") or "unknown")
            entity_types[et] = entity_types.get(et, 0) + 1
        print(f"Selected {len(selected)} / {len(items)} items:")
        for k, v in sorted(prefixes.items(), key=lambda x: -x[1]):
            print(f"  PK {k}: {v}")
        for k, v in sorted(entity_types.items(), key=lambda x: -x[1]):
            print(f"  entity_type {k}: {v}")

        if args.dry_run:
            print("[dry-run] skip DynamoDB write")
        else:
            print("Writing to destination table (overwrite matching PK/SK)…")
            n = batch_write(args.dest_table, args.region, selected)
            print(f"Wrote {n} items to {args.dest_table}")

    if not args.skip_cognito:
        print("\nMigrating Cognito users…")
        if args.dry_run:
            users = list_cognito_users(args.source_pool, args.region)
            print(f"[dry-run] would migrate {len(users)} users")
            for u in users:
                attrs = {a["Name"]: a["Value"] for a in u.get("Attributes", [])}
                print(f"  - {attrs.get('email') or u['Username']}")
        else:
            report = migrate_cognito(
                source_pool=args.source_pool,
                dest_pool=args.dest_pool,
                region=args.region,
                force=args.force_cognito,
            )
            print(
                f"Created/updated: {len(report['created'])}, "
                f"skipped: {len(report['skipped'])}, "
                f"errors: {len(report['errors'])}"
            )
            for e in report["errors"]:
                print(f"  ERROR {e}")
            sub_map = report.get("sub_map") or {}
            if sub_map and not args.skip_dynamodb and not args.dry_run:
                print("\nRemapping USER# partitions to new Cognito sub values…")
                w, d = remap_user_items(args.dest_table, args.region, sub_map)
                print(f"Remapped put={w} delete={d} for {len(sub_map)} users")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
