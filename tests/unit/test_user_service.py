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


@pytest.mark.unit
def test_public_profile_notices_opt_in(dynamodb_table):
    """H2: content_notices only when include_content_notices=True."""
    p = user_service.ensure_user_profile("u-notices", email="n@y.com", nickname="Ned")
    fast = user_service.public_profile(p, include_content_notices=False)
    assert fast.get("content_notices") == []
    full = user_service.public_profile(p, include_content_notices=True)
    assert "content_notices" in full
    assert isinstance(full["content_notices"], list)


@pytest.mark.unit
def test_facebook_follow_grants_six_month_subscription(dynamodb_table):
    now = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
    with freeze_time(now):
        user_service.ensure_user_profile("u-fb", email="f@b.com", nickname="Fay")
        updated = user_service.claim_facebook_follow(
            "u-fb", display_name="Fay", handle="fay.fb"
        )
        assert updated["facebook_followed"] is True
        assert updated["subscription_status"] == "active"
        assert updated["subscription_source"] == "facebook_follow"
        end = datetime.fromisoformat(updated["subscription_ends_at"].replace("Z", "+00:00"))
        # Extends from any remaining trial window, then + ~180 days
        assert end >= now + timedelta(days=179)
        assert end <= now + timedelta(days=220)
        pub = user_service.public_profile(updated, include_content_notices=False)
        assert pub["facebook_followed"] is True
        assert pub["facebook_subscription_active"] is True
        assert pub["ads_may_appear"] is False
        assert pub["ad_free_active"] is True


@pytest.mark.unit
def test_facebook_engagement_extends_ad_free(dynamodb_table):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with freeze_time(start):
        user_service.ensure_user_profile("u-eng", email="e@b.com")
        user_service.claim_facebook_follow("u-eng")
    later = start + timedelta(days=100)
    with freeze_time(later):
        # Engagement window (90d) expired → ads may appear until new engagement
        profile = user_service.get_profile("u-eng")
        mid = user_service.public_profile(profile, include_content_notices=False)
        assert mid["ads_may_appear"] is True
        updated = user_service.claim_facebook_engagement(
            "u-eng", kind="feature_request", text="Add more levels"
        )
        pub = user_service.public_profile(updated, include_content_notices=False)
        assert pub["ad_free_active"] is True
        assert pub["ads_may_appear"] is False
        assert pub["last_facebook_engagement_at"]
