"""Unit tests for user/subscription service."""

from datetime import datetime, timedelta, timezone

import pytest
from freezegun import freeze_time

from app.services import user_service


@pytest.mark.unit
def test_ensure_profile_and_trial(dynamodb_table):
    now = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    with freeze_time(now):
        p = user_service.ensure_user_profile("u1", email="a@b.com")
    assert p["subscription_status"] == "trial"
    assert p["email"] == "a@b.com"
    assert user_service.is_subscription_active(p, now=now)

    # Idempotent
    p2 = user_service.ensure_user_profile("u1")
    assert p2["user_id"] == "u1"


@pytest.mark.unit
def test_trial_expiry(dynamodb_table):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with freeze_time(start):
        p = user_service.ensure_user_profile("u2")
    later = start + timedelta(days=31)
    assert not user_service.is_subscription_active(p, now=later)
    assert user_service.is_subscription_active(p, now=start + timedelta(days=10))


@pytest.mark.unit
def test_public_profile(dynamodb_table):
    p = user_service.ensure_user_profile("u3", email="x@y.com")
    pub = user_service.public_profile(p)
    assert "PK" not in pub
    assert pub["subscription_active"] is True
    assert pub["email"] == "x@y.com"
