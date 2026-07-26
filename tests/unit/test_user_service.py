"""Unit tests for user/subscription service."""

from datetime import datetime, timedelta, timezone

import pytest
from freezegun import freeze_time

from app.services import user_service


@pytest.mark.unit
def test_ensure_profile_and_trial(dynamodb_table):
    now = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    with freeze_time(now):
        p = user_service.ensure_user_profile("u1", email="a@b.com", nickname="Ada")
    assert p["email"] == "a@b.com"
    assert p["nickname"] == "Ada"
    # Free access — subscription product not enforced
    assert user_service.is_subscription_active(p, now=now)

    # Idempotent; backfill nickname only when empty
    p2 = user_service.ensure_user_profile("u1", nickname="Other")
    assert p2["user_id"] == "u1"
    assert p2["nickname"] == "Ada"


@pytest.mark.unit
def test_free_access_always_active(dynamodb_table):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with freeze_time(start):
        p = user_service.ensure_user_profile("u2")
    later = start + timedelta(days=400)
    assert user_service.is_subscription_active(p, now=later)


@pytest.mark.unit
def test_update_nickname(dynamodb_table):
    user_service.ensure_user_profile("u4", email="n@x.com")
    updated = user_service.update_profile("u4", nickname="Nova")
    assert updated["nickname"] == "Nova"
    pub = user_service.public_profile(updated)
    assert pub["nickname"] == "Nova"
    assert pub["display_name"] == "Nova"


@pytest.mark.unit
def test_public_profile(dynamodb_table):
    p = user_service.ensure_user_profile("u3", email="x@y.com", nickname="Sam")
    pub = user_service.public_profile(p)
    assert "PK" not in pub
    assert pub["subscription_active"] is True
    assert pub["email"] == "x@y.com"
    assert pub["nickname"] == "Sam"
