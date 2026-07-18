"""HTTP response helpers for API Gateway HTTP API."""

from __future__ import annotations

import json
from typing import Any


def _default(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def api_response(
    status_code: int,
    body: Any = None,
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build an API Gateway HTTP API proxy response."""
    resp_headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
    }
    if headers:
        resp_headers.update(headers)

    if body is None:
        payload = ""
    elif isinstance(body, str):
        payload = body
    else:
        payload = json.dumps(body, default=_default)

    return {
        "statusCode": status_code,
        "headers": resp_headers,
        "body": payload,
    }


def ok(body: Any = None, status_code: int = 200) -> dict[str, Any]:
    return api_response(status_code, body if body is not None else {"ok": True})


def created(body: Any) -> dict[str, Any]:
    return api_response(201, body)


def no_content() -> dict[str, Any]:
    return api_response(204, None)


def error(
    status_code: int,
    message: str,
    *,
    code: str | None = None,
    details: Any = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"error": message}
    if code:
        body["code"] = code
    if details is not None:
        body["details"] = details
    return api_response(status_code, body)


def bad_request(message: str, details: Any = None) -> dict[str, Any]:
    return error(400, message, code="BAD_REQUEST", details=details)


def unauthorized(message: str = "Unauthorized") -> dict[str, Any]:
    return error(401, message, code="UNAUTHORIZED")


def forbidden(message: str = "Forbidden") -> dict[str, Any]:
    return error(403, message, code="FORBIDDEN")


def not_found(message: str = "Not found") -> dict[str, Any]:
    return error(404, message, code="NOT_FOUND")


def conflict(message: str) -> dict[str, Any]:
    return error(409, message, code="CONFLICT")


def unprocessable(message: str, details: Any = None) -> dict[str, Any]:
    return error(422, message, code="VALIDATION_ERROR", details=details)


def payment_required(message: str = "Subscription required") -> dict[str, Any]:
    return error(402, message, code="PAYMENT_REQUIRED")


def server_error(message: str = "Internal server error") -> dict[str, Any]:
    return error(500, message, code="INTERNAL_ERROR")
