"""Test helpers shared across unit/integration tests."""

from __future__ import annotations


def make_event(
    method: str,
    path: str,
    *,
    headers: dict | None = None,
    body: str | None = None,
    qs: dict | None = None,
) -> dict:
    return {
        "version": "2.0",
        "routeKey": f"{method} {path}",
        "rawPath": path,
        "headers": headers or {},
        "queryStringParameters": qs,
        "requestContext": {
            "http": {"method": method, "path": path},
            "authorizer": {},
        },
        "body": body,
        "isBase64Encoded": False,
    }
