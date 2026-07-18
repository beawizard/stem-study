"""Unit tests for auth context extraction."""

import pytest

from app.auth import AuthError, UserContext, get_user_context, require_admin


def test_jwt_claims_http_api():
    event = {
        "requestContext": {
            "authorizer": {
                "jwt": {
                    "claims": {
                        "sub": "abc-123",
                        "email": "a@b.com",
                        "cognito:groups": ["admin"],
                    }
                }
            }
        }
    }
    user = get_user_context(event)
    assert user.user_id == "abc-123"
    assert user.is_admin
    assert user.email == "a@b.com"


def test_rest_api_claims():
    event = {
        "requestContext": {
            "authorizer": {
                "claims": {
                    "sub": "u2",
                    "cognito:groups": "users,admin",
                }
            }
        }
    }
    user = get_user_context(event)
    assert user.user_id == "u2"
    assert user.is_admin


def test_missing_auth_raises(monkeypatch):
    monkeypatch.delenv("ALLOW_TEST_AUTH", raising=False)
    with pytest.raises(AuthError):
        get_user_context({"requestContext": {}, "headers": {}})


def test_test_auth_bypass(monkeypatch):
    monkeypatch.setenv("ALLOW_TEST_AUTH", "1")
    event = {
        "requestContext": {},
        "headers": {
            "x-test-user": "test-user",
            "x-test-groups": "admin",
            "x-test-email": "t@e.com",
        },
    }
    user = get_user_context(event)
    assert user.user_id == "test-user"
    assert user.is_admin


def test_require_admin():
    require_admin(UserContext(user_id="a", groups=["admin"]))
    with pytest.raises(AuthError) as ei:
        require_admin(UserContext(user_id="b", groups=[]))
    assert ei.value.status == 403


def test_groups_as_list_string():
    event = {
        "requestContext": {
            "authorizer": {
                "jwt": {"claims": {"sub": "x", "cognito:groups": '["admin","users"]'}}
            }
        }
    }
    user = get_user_context(event)
    assert "admin" in user.groups
