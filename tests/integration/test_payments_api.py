"""Integration tests – trial expiry and GCash payment flow."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from freezegun import freeze_time

from app.handler import handler
from app.services import user_service
from tests.helpers import make_event


@pytest.mark.integration
def test_payment_submit_and_admin_verify(dynamodb_table, user_headers, admin_headers):
    # Ensure profile
    handler(make_event("GET", "/me", headers=user_headers))

    resp = handler(
        make_event(
            "POST",
            "/payments",
            headers=user_headers,
            body=json.dumps(
                {"gcash_reference": "GCASHREF001", "amount_php": 99, "notes": "Jan"}
            ),
        )
    )
    assert resp["statusCode"] == 201, resp["body"]
    payment = json.loads(resp["body"])
    assert payment["status"] == "pending"
    payment_id = payment["payment_id"]
    user_id = user_headers["x-test-user"]

    # User profile pending
    me = json.loads(handler(make_event("GET", "/me", headers=user_headers))["body"])
    assert me["subscription_status"] == "pending_payment"

    # Admin lists pending
    resp = handler(make_event("GET", "/admin/payments", headers=admin_headers))
    assert resp["statusCode"] == 200
    pending = json.loads(resp["body"])["payments"]
    assert any(p["payment_id"] == payment_id for p in pending)

    # Verify
    resp = handler(
        make_event(
            "POST",
            f"/admin/payments/{user_id}/{payment_id}/verify",
            headers=admin_headers,
            body=json.dumps({"status": "verified", "notes": "ok"}),
        )
    )
    assert resp["statusCode"] == 200, resp["body"]
    assert json.loads(resp["body"])["status"] == "verified"

    me = json.loads(handler(make_event("GET", "/me", headers=user_headers))["body"])
    assert me["subscription_status"] == "active"
    assert me["subscription_active"] is True


@pytest.mark.integration
def test_expired_trial_blocks_study(dynamodb_table, admin_headers, user_headers):
    # Seed content
    handler(make_event("POST", "/admin/seed", headers=admin_headers))

    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with freeze_time(start):
        handler(make_event("GET", "/me", headers=user_headers))

    # Jump past trial
    with freeze_time(start + timedelta(days=45)):
        resp = handler(
            make_event(
                "POST",
                "/study/sessions",
                headers=user_headers,
                body=json.dumps({"subject_id": "math", "level_id": "l1"}),
            )
        )
        assert resp["statusCode"] == 402

        # Can still access /me and payments
        assert handler(make_event("GET", "/me", headers=user_headers))["statusCode"] == 200
        resp = handler(
            make_event(
                "POST",
                "/payments",
                headers=user_headers,
                body=json.dumps({"gcash_reference": "LATE001", "amount_php": 99}),
            )
        )
        assert resp["statusCode"] == 201


@pytest.mark.integration
def test_reject_payment(dynamodb_table, user_headers, admin_headers):
    handler(make_event("GET", "/me", headers=user_headers))
    # Force expire
    user_service.update_subscription(user_headers["x-test-user"], status="expired")

    resp = handler(
        make_event(
            "POST",
            "/payments",
            headers=user_headers,
            body=json.dumps({"gcash_reference": "BAD001", "amount_php": 50}),
        )
    )
    payment_id = json.loads(resp["body"])["payment_id"]
    user_id = user_headers["x-test-user"]

    resp = handler(
        make_event(
            "POST",
            f"/admin/payments/{user_id}/{payment_id}/verify",
            headers=admin_headers,
            body=json.dumps({"status": "rejected"}),
        )
    )
    assert resp["statusCode"] == 200
    me = json.loads(handler(make_event("GET", "/me", headers=user_headers))["body"])
    assert me["subscription_status"] == "expired"


@pytest.mark.integration
def test_health_no_auth(dynamodb_table):
    resp = handler(make_event("GET", "/health", headers={}))
    # health is before auth in dispatch - wait, currently get_user_context is called after health check
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["status"] == "healthy"
