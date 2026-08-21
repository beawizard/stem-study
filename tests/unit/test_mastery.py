"""Unit tests for Mastery collections."""

from __future__ import annotations

import pytest

from app.services import mastery_service, subject_service
from app.validation import MasteryCreate, SubjectCreate


def _seed_math_topics(dynamodb_table):
    subject_service.create_subject(
        SubjectCreate(category="Mathematics", topic="Arithmetic (Addition) - Level 1")
    )
    subject_service.create_subject(
        SubjectCreate(category="Mathematics", topic="Arithmetic (Addition) - Level 2")
    )
    subject_service.create_subject(
        SubjectCreate(category="Mathematics", topic="Arithmetic (Subtraction) - Level 1")
    )
    subject_service.create_subject(
        SubjectCreate(category="Mathematics", topic="Fractions - Level 1")
    )


@pytest.mark.unit
def test_create_and_list_personal_mastery(dynamodb_table):
    _seed_math_topics(dynamodb_table)
    data = MasteryCreate(
        name="Add & Subtract",
        category="Mathematics",
        topics=["Arithmetic (Addition)", "Arithmetic (Subtraction)"],
        start_date="2026-08-01",
        end_date="2026-09-30",
        shared=False,
    )
    created = mastery_service.create_mastery("u1", data, is_admin=False)
    assert created["name"] == "Add & Subtract"
    assert created["shared"] is False
    assert created["status"] == "published"
    assert len(created["topics"]) == 2
    assert len(created["subject_ids"]) >= 2

    listed = mastery_service.list_mastery_for_user("u1")
    assert len(listed) == 1
    assert listed[0]["mastery_id"] == created["mastery_id"]

    # Other user does not see personal collection
    assert mastery_service.list_mastery_for_user("u2") == []


@pytest.mark.unit
def test_shared_mastery_visible_to_all(dynamodb_table):
    _seed_math_topics(dynamodb_table)
    data = MasteryCreate(
        name="School Pack",
        category="Mathematics",
        topics=["Arithmetic (Addition)", "Fractions"],
        start_date="2026-01-01",
        end_date="2026-12-31",
        shared=True,
    )
    # Non-admin shared flag is ignored — personal only
    personal = mastery_service.create_mastery("learner", data, is_admin=False)
    assert personal["shared"] is False
    assert mastery_service.list_mastery_for_user("other") == []

    # Admin create with shared=True is visible to everyone
    admin_data = MasteryCreate(
        name="School Pack Auto",
        category="Mathematics",
        topics=["Arithmetic (Addition)", "Fractions"],
        start_date="2026-01-01",
        end_date="2026-12-31",
        shared=True,
    )
    shared = mastery_service.create_mastery("admin-1", admin_data, is_admin=True)
    assert shared["shared"] is True
    others = mastery_service.list_mastery_for_user("other")
    assert any(c["mastery_id"] == shared["mastery_id"] for c in others)
    got = mastery_service.get_mastery("other", shared["mastery_id"])
    assert got["name"] == "School Pack Auto"


@pytest.mark.unit
def test_mastery_requires_two_topics():
    with pytest.raises(Exception):
        MasteryCreate(
            name="One only",
            category="Mathematics",
            topics=["Only One"],
            start_date="2026-01-01",
            end_date="2026-01-31",
        )


@pytest.mark.unit
def test_window_status():
    from datetime import date

    assert (
        mastery_service.window_status("2026-01-01", "2026-12-31", today=date(2026, 6, 1))
        == "active"
    )
    assert (
        mastery_service.window_status("2026-09-01", "2026-12-31", today=date(2026, 6, 1))
        == "upcoming"
    )
    assert (
        mastery_service.window_status("2026-01-01", "2026-03-01", today=date(2026, 6, 1))
        == "ended"
    )


@pytest.mark.unit
def test_update_and_delete_mastery(dynamodb_table):
    _seed_math_topics(dynamodb_table)
    data = MasteryCreate(
        name="Pack A",
        category="Mathematics",
        topics=["Arithmetic (Addition)", "Arithmetic (Subtraction)"],
        start_date="2026-08-01",
        end_date="2026-09-30",
    )
    created = mastery_service.create_mastery("u1", data, is_admin=False)
    mid = created["mastery_id"]

    updated = mastery_service.update_mastery(
        "u1",
        mid,
        MasteryCreate(
            name="Pack A Renamed",
            category="Mathematics",
            topics=["Arithmetic (Addition)", "Fractions"],
            start_date="2026-08-01",
            end_date="2026-10-15",
        ),
        is_admin=False,
    )
    assert updated["name"] == "Pack A Renamed"
    assert "Fractions" in updated["topics"]
    assert updated["can_manage"] is True

    # Non-owner cannot see/edit a personal pack (not found — no existence leak)
    with pytest.raises(mastery_service.MasteryNotFound):
        mastery_service.update_mastery(
            "u2",
            mid,
            MasteryCreate(
                name="Hijack",
                category="Mathematics",
                topics=["Arithmetic (Addition)", "Fractions"],
                start_date="2026-08-01",
                end_date="2026-10-15",
            ),
            is_admin=False,
        )

    # Admin can edit someone else's pack
    admin_updated = mastery_service.update_mastery(
        "admin-x",
        mid,
        MasteryCreate(
            name="Admin Edit",
            category="Mathematics",
            topics=["Arithmetic (Addition)", "Fractions"],
            start_date="2026-08-01",
            end_date="2026-10-15",
            shared=True,
        ),
        is_admin=True,
    )
    assert admin_updated["name"] == "Admin Edit"
    assert admin_updated["shared"] is True

    mastery_service.soft_delete_mastery("admin-x", mid, is_admin=True)
    with pytest.raises(mastery_service.MasteryNotFound):
        mastery_service.get_mastery("u1", mid)
    assert mastery_service.list_mastery_for_user("u1") == []
