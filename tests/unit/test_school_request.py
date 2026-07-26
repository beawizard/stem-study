"""Unit tests for learner school requests and admin approval."""

import pytest

from app.services import school_service, user_service


@pytest.mark.unit
def test_request_school_is_pending_and_hidden_from_public_list(dynamodb_table, monkeypatch):
    monkeypatch.delenv("ADMIN_NOTIFY_EMAIL", raising=False)
    school = school_service.request_school(
        name="New Horizon Academy",
        city="Davao City",
        province="Davao del Sur",
        requester_email="learner@example.com",
    )
    assert school["school_id"]
    assert school["status"] == "pending"
    assert school["pending"] is True
    assert "pending" in (school["label"] or "").lower()

    public = school_service.list_schools()
    assert all(s["school_id"] != school["school_id"] for s in public)

    admin = school_service.list_schools(include_pending=True)
    ids = {s["school_id"] for s in admin}
    assert school["school_id"] in ids


@pytest.mark.unit
def test_approve_school_updates_linked_user_profile(dynamodb_table, monkeypatch):
    monkeypatch.delenv("ADMIN_NOTIFY_EMAIL", raising=False)
    school = school_service.request_school(
        name="Bayanihan High",
        city="Quezon City",
        province="Metro Manila",
        requester_email="kid@example.com",
    )
    sid = school["school_id"]

    user_service.ensure_user_profile("u-req-1", email="kid@example.com", nickname="Kid")
    updated = user_service.update_profile("u-req-1", school_id=sid)
    assert updated["school_id"] == sid
    assert "pending" in (updated.get("school_name") or "").lower()

    # Linked on profile update
    raw = school_service.get_school(sid)
    assert "u-req-1" in (raw.get("linked_user_ids") or [])

    approved = school_service.approve_school(sid)
    assert approved["status"] == "active"
    assert approved["pending"] is False
    assert "pending" not in (approved["label"] or "").lower()

    profile = user_service.get_profile("u-req-1")
    assert profile["school_id"] == sid
    assert "pending" not in (profile.get("school_name") or "").lower()
    assert "Bayanihan" in (profile.get("school_name") or "")


@pytest.mark.unit
def test_active_school_in_public_list(dynamodb_table):
    s = school_service.create_school(name="Rizal ES", city="Cebu City", province="Cebu")
    public = school_service.list_schools()
    assert any(x["school_id"] == s["school_id"] for x in public)
