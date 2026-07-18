"""Unit tests for recommendation rules."""

from app.services.insights_service import build_recommendation


def test_mastery_fast_accurate():
    r = build_recommendation(
        accuracy=0.95,
        avg_ms_per_question=2000,
        passed=True,
        subject_id="math",
        level_id="l1",
        user_id="u",
    )
    assert "mastery" in r["tags"]
    assert r["passed"] is True
    assert r["actions"]


def test_rushed_low_accuracy():
    r = build_recommendation(
        accuracy=0.4,
        avg_ms_per_question=1000,
        passed=False,
        subject_id="math",
        level_id="l1",
        user_id="u",
    )
    assert "needs_review" in r["tags"]
    assert "rushed" in r["tags"]


def test_almost_there():
    r = build_recommendation(
        accuracy=0.7,
        avg_ms_per_question=10000,
        passed=False,
        subject_id="math",
        level_id="l2",
        user_id="u",
    )
    assert "almost_there" in r["tags"]


def test_slow_but_accurate():
    r = build_recommendation(
        accuracy=0.92,
        avg_ms_per_question=45000,
        passed=True,
        subject_id="math",
        level_id="l1",
        user_id="u",
    )
    assert "careful_mastery" in r["tags"]
