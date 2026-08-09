"""Unit tests for XP computation and leaderboard ranking."""

from __future__ import annotations

import pytest

from app import db, keys
from app.services import user_service


def _put_progress(
    user_id: str,
    subject_id: str,
    level_id: str,
    *,
    badge: str | None,
    status: str = "completed",
    best_elapsed_ms: int | None = None,
) -> None:
    db.put_item(
        {
            "PK": keys.user_pk(user_id),
            "SK": keys.progress_sk(subject_id, level_id),
            "entity_type": "PROGRESS",
            "user_id": user_id,
            "subject_id": subject_id,
            "level_id": level_id,
            "status": status,
            "speed_badge": badge or "",
            "best_elapsed_ms": best_elapsed_ms,
            "deleted_at": "",
        }
    )


@pytest.mark.unit
def test_xp_points_for_badge():
    assert user_service.xp_points_for_badge("legendary_wizard") == 5
    assert user_service.xp_points_for_badge("superb_advanced") == 3
    assert user_service.xp_points_for_badge("cool_novice") == 1
    assert user_service.xp_points_for_badge(None) == 0
    assert user_service.xp_points_for_badge("unknown") == 0


@pytest.mark.unit
def test_compute_xp_sums_completed_badges(dynamodb_table):
    user_service.ensure_user_profile("u-xp", email="a@b.com", nickname="Ada", grade="Grade 5")
    _put_progress("u-xp", "math", "l1", badge="legendary_wizard")  # 5
    _put_progress("u-xp", "math", "l2", badge="superb_advanced")  # 3
    _put_progress("u-xp", "math", "l3", badge="cool_novice")  # 1
    _put_progress("u-xp", "math", "l4", badge="legendary_wizard", status="failed")  # 0
    assert user_service.compute_xp_from_progress("u-xp") == 9


@pytest.mark.unit
def test_compute_xp_derives_badge_from_elapsed_when_missing(dynamodb_table):
    """Pre-badge completions: derive from best time (≤30s = legendary = 5)."""
    user_service.ensure_user_profile("u-legacy", email="l@b.com", nickname="Lee")
    _put_progress(
        "u-legacy",
        "math",
        "l1",
        badge=None,
        status="completed",
        best_elapsed_ms=20_000,  # legendary
    )
    assert user_service.compute_xp_from_progress("u-legacy") == 5


@pytest.mark.unit
def test_refresh_user_xp_and_leaderboard_order(dynamodb_table):
    user_service.ensure_user_profile(
        "u-high", email="h@b.com", nickname="High Scorer", grade="Grade 6"
    )
    user_service.ensure_user_profile(
        "u-mid", email="m@b.com", nickname="Mid Scorer", grade="Grade 4"
    )
    user_service.ensure_user_profile(
        "u-low", email="l@b.com", nickname="Low Scorer", grade="Grade 3"
    )
    _put_progress("u-high", "math", "l1", badge="legendary_wizard")  # 5
    _put_progress("u-high", "math", "l2", badge="legendary_wizard")  # 5 → 10
    _put_progress("u-mid", "math", "l1", badge="superb_advanced")  # 3
    _put_progress("u-low", "math", "l1", badge="cool_novice")  # 1

    user_service.refresh_user_xp("u-high")
    user_service.refresh_user_xp("u-mid")
    user_service.refresh_user_xp("u-low")

    board = user_service.list_leaderboard(limit=10)
    assert len(board) == 3
    assert board[0]["rank"] == 1
    assert board[0]["name"] == "High Scorer"
    assert board[0]["xp"] == 10
    assert board[0]["grade"] == "Grade 6"
    assert board[1]["name"] == "Mid Scorer"
    assert board[1]["xp"] == 3
    assert board[2]["name"] == "Low Scorer"
    assert board[2]["xp"] == 1

    assert user_service.rank_for_xp(10, "u-high") == 1
    assert user_service.rank_for_xp(3, "u-mid") == 2
    assert user_service.rank_for_xp(1, "u-low") == 3


@pytest.mark.unit
def test_public_profile_includes_xp_and_rank(dynamodb_table):
    user_service.ensure_user_profile(
        "u-pub", email="p@b.com", nickname="Pat", grade="Grade 2"
    )
    _put_progress("u-pub", "math", "l1", badge="superb_advanced")
    refreshed = user_service.refresh_user_xp("u-pub")
    pub = user_service.public_profile(refreshed, include_content_notices=False)
    assert pub["xp"] == 3
    assert pub["rank"] == 1
    assert pub["grade"] == "Grade 2"


@pytest.mark.unit
def test_badge_upgrade_recomputes_xp(dynamodb_table):
    """Re-taking a set with a better badge replaces points (not stacks)."""
    user_service.ensure_user_profile("u-up", email="u@b.com", nickname="Up")
    _put_progress("u-up", "math", "l1", badge="cool_novice")
    user_service.refresh_user_xp("u-up")
    assert user_service.compute_xp_from_progress("u-up") == 1

    # Same set, better badge only
    _put_progress("u-up", "math", "l1", badge="legendary_wizard")
    user_service.refresh_user_xp("u-up")
    assert user_service.compute_xp_from_progress("u-up") == 5
    board = user_service.list_leaderboard()
    assert board[0]["xp"] == 5
