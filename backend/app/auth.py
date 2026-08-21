"""Auth context extraction from API Gateway Cognito JWT authorizer claims."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any


class AuthError(Exception):
    """Raised when authentication/authorization fails."""

    def __init__(self, message: str = "Unauthorized", status: int = 401):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class UserContext:
    user_id: str
    email: str | None = None
    groups: list[str] = field(default_factory=list)
    username: str | None = None
    nickname: str | None = None

    @property
    def is_admin(self) -> bool:
        return "admin" in self.groups or "admins" in self.groups


def _claims_from_event(event: dict[str, Any]) -> dict[str, Any]:
    """Extract JWT claims from HTTP API or REST API event shapes."""
    rc = event.get("requestContext") or {}

    # HTTP API JWT authorizer
    authorizer = rc.get("authorizer") or {}
    jwt = authorizer.get("jwt") or {}
    if jwt.get("claims"):
        return jwt["claims"]

    # Lambda authorizer / REST API Cognito
    if authorizer.get("claims"):
        return authorizer["claims"]

    # Some proxies nest under lambda
    lambda_auth = authorizer.get("lambda") or {}
    if lambda_auth.get("claims"):
        return lambda_auth["claims"]

    return {}


def _parse_groups(groups_claim) -> list[str]:
    if not groups_claim:
        return []
    if isinstance(groups_claim, (list, tuple)):
        return [str(g).strip() for g in groups_claim if str(g).strip()]
    if isinstance(groups_claim, str):
        raw = groups_claim.strip()
        if raw.startswith("["):
            try:
                parsed = json.loads(raw.replace("'", '"'))
                if isinstance(parsed, list):
                    return [str(g).strip() for g in parsed if str(g).strip()]
            except json.JSONDecodeError:
                pass
        return [
            g.strip()
            for g in raw.strip("[]").replace('"', "").split(",")
            if g.strip()
        ]
    return [str(groups_claim).strip()]


def _claims_from_bearer(event: dict[str, Any]) -> dict[str, Any]:
    headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}
    auth = headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return {}
    token = auth.split(" ", 1)[1].strip()
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    try:
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")))
    except Exception:
        return {}


def get_user_context(event: dict[str, Any]) -> UserContext:
    """Build UserContext from the API Gateway event. Raises AuthError if missing."""
    claims = _claims_from_event(event)
    if not claims:
        # Dev/test bypass: allow X-Test-User header when ALLOW_TEST_AUTH=1
        import os

        headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        if os.environ.get("ALLOW_TEST_AUTH") == "1" and headers.get("x-test-user"):
            groups_raw = headers.get("x-test-groups", "")
            groups = [g.strip() for g in groups_raw.split(",") if g.strip()]
            return UserContext(
                user_id=headers["x-test-user"],
                email=headers.get("x-test-email"),
                groups=groups,
                username=headers.get("x-test-user"),
                nickname=headers.get("x-test-nickname") or headers.get("x-test-name"),
            )
        raise AuthError("Missing authentication claims")

    user_id = claims.get("sub") or claims.get("cognito:username")
    if not user_id:
        raise AuthError("Token missing subject")

    groups = _parse_groups(claims.get("cognito:groups") or claims.get("groups"))
    # HTTP API JWT authorizer sometimes omits cognito:groups; fall back to the
    # already-verified Bearer token payload (ID token includes groups).
    if not groups:
        groups = _parse_groups(_claims_from_bearer(event).get("cognito:groups"))

    nick = claims.get("nickname") or claims.get("name") or claims.get("preferred_username")
    if nick is not None:
        nick = str(nick).strip() or None

    return UserContext(
        user_id=str(user_id),
        email=claims.get("email"),
        groups=groups,
        username=claims.get("cognito:username") or claims.get("username"),
        nickname=nick,
    )


def require_admin(user: UserContext) -> None:
    if not user.is_admin:
        raise AuthError("Admin privileges required", status=403)
