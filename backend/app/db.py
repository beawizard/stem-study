"""DynamoDB access layer for single-table design."""

from __future__ import annotations

import os
from decimal import Decimal
from functools import lru_cache
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key


def _to_dynamo(obj: Any) -> Any:
    """Convert floats to Decimal for DynamoDB."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _to_dynamo(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_dynamo(v) for v in obj]
    return obj


def _from_dynamo(obj: Any) -> Any:
    """Convert Decimal back to int/float for JSON."""
    if isinstance(obj, Decimal):
        if obj % 1 == 0:
            return int(obj)
        return float(obj)
    if isinstance(obj, dict):
        return {k: _from_dynamo(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_from_dynamo(v) for v in obj]
    return obj


@lru_cache(maxsize=1)
def get_table_name() -> str:
    name = os.environ.get("TABLE_NAME")
    if not name:
        raise RuntimeError("TABLE_NAME environment variable is required")
    return name


def get_resource():
    endpoint = os.environ.get("AWS_ENDPOINT_URL")
    region = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-1"))
    kwargs: dict[str, Any] = {"region_name": region}
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    return boto3.resource("dynamodb", **kwargs)


def get_table():
    return get_resource().Table(get_table_name())


def put_item(item: dict[str, Any], *, condition: str | None = None) -> None:
    kwargs: dict[str, Any] = {"Item": _to_dynamo(item)}
    if condition:
        kwargs["ConditionExpression"] = condition
    get_table().put_item(**kwargs)


def batch_put_items(items: list[dict[str, Any]]) -> int:
    """Bulk put items (best-effort; no condition expressions). Returns count written."""
    if not items:
        return 0
    table = get_table()
    written = 0
    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=_to_dynamo(item))
            written += 1
    return written


def get_item(pk: str, sk: str) -> dict[str, Any] | None:
    resp = get_table().get_item(Key={"PK": pk, "SK": sk})
    item = resp.get("Item")
    return _from_dynamo(item) if item else None


def update_item(
    pk: str,
    sk: str,
    updates: dict[str, Any],
    *,
    condition: str | None = None,
) -> dict[str, Any]:
    """Partial update; returns updated attributes."""
    if not updates:
        existing = get_item(pk, sk)
        if existing is None:
            raise KeyError("Item not found")
        return existing

    expr_names: dict[str, str] = {}
    expr_values: dict[str, Any] = {}
    parts: list[str] = []
    for i, (k, v) in enumerate(updates.items()):
        nk, vk = f"#k{i}", f":v{i}"
        expr_names[nk] = k
        expr_values[vk] = _to_dynamo(v)
        parts.append(f"{nk} = {vk}")

    kwargs: dict[str, Any] = {
        "Key": {"PK": pk, "SK": sk},
        "UpdateExpression": "SET " + ", ".join(parts),
        "ExpressionAttributeNames": expr_names,
        "ExpressionAttributeValues": expr_values,
        "ReturnValues": "ALL_NEW",
    }
    if condition:
        kwargs["ConditionExpression"] = condition

    resp = get_table().update_item(**kwargs)
    return _from_dynamo(resp["Attributes"])


def delete_item(pk: str, sk: str) -> None:
    get_table().delete_item(Key={"PK": pk, "SK": sk})


def query_pk(
    pk: str,
    *,
    sk_begins_with: str | None = None,
    filter_expr=None,
    limit: int | None = None,
    scan_forward: bool = True,
) -> list[dict[str, Any]]:
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("PK").eq(pk),
        "ScanIndexForward": scan_forward,
    }
    if sk_begins_with:
        kwargs["KeyConditionExpression"] = Key("PK").eq(pk) & Key("SK").begins_with(
            sk_begins_with
        )
    if filter_expr is not None:
        kwargs["FilterExpression"] = filter_expr
    if limit is not None:
        kwargs["Limit"] = limit

    items: list[dict[str, Any]] = []
    table = get_table()
    while True:
        resp = table.query(**kwargs)
        items.extend(_from_dynamo(resp.get("Items", [])))
        lek = resp.get("LastEvaluatedKey")
        if not lek or (limit is not None and len(items) >= limit):
            break
        kwargs["ExclusiveStartKey"] = lek
    if limit is not None:
        return items[:limit]
    return items


def query_gsi1(
    gsi1pk: str,
    *,
    sk_begins_with: str | None = None,
    limit: int | None = None,
    scan_forward: bool = True,
) -> list[dict[str, Any]]:
    kwargs: dict[str, Any] = {
        "IndexName": "GSI1",
        "KeyConditionExpression": Key("GSI1PK").eq(gsi1pk),
        "ScanIndexForward": scan_forward,
    }
    if sk_begins_with:
        kwargs["KeyConditionExpression"] = Key("GSI1PK").eq(gsi1pk) & Key(
            "GSI1SK"
        ).begins_with(sk_begins_with)
    if limit is not None:
        kwargs["Limit"] = limit

    items: list[dict[str, Any]] = []
    table = get_table()
    while True:
        resp = table.query(**kwargs)
        items.extend(_from_dynamo(resp.get("Items", [])))
        lek = resp.get("LastEvaluatedKey")
        if not lek or (limit is not None and len(items) >= limit):
            break
        kwargs["ExclusiveStartKey"] = lek
    if limit is not None:
        return items[:limit]
    return items


def soft_delete_filter():
    """Filter expression excluding soft-deleted items (deleted_at not present or empty)."""
    return Attr("deleted_at").not_exists() | Attr("deleted_at").eq("")


def clear_caches() -> None:
    """Reset cached resources (for tests)."""
    get_table_name.cache_clear()
