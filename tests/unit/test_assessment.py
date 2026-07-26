"""Unit tests for placement assessment sampling and proficiency."""

from app.services.study_service import (
    BADGE_ADVANCED,
    BADGE_LEGENDARY,
    BADGE_NOVICE,
    _group_levels_by_major,
    _is_proficient,
    base_topic_name,
    major_from_subject,
    sample_questions_across_sets,
    sample_questions_balanced,
    suggest_starting_major,
)


def _band(major: int, proficient: bool) -> dict:
    return {"major": major, "proficient": proficient, "name": f"Level {major}"}


def test_suggest_lowest_failed_not_highest_proficient():
    """Proficient 1–3 and 6, failed 4–5 → suggest Level 4 (first gap)."""
    results = [
        _band(1, True),
        _band(2, True),
        _band(3, True),
        _band(4, False),
        _band(5, False),
        _band(6, True),
    ]
    major, mastered, msg = suggest_starting_major(results)
    assert major == 4
    assert mastered is False
    assert "Level 4" in msg
    assert "Level 6" not in msg or "through Level 3" in msg


def test_suggest_all_proficient_is_highest():
    results = [_band(m, True) for m in (1, 2, 3)]
    major, mastered, msg = suggest_starting_major(results)
    assert major == 3
    assert mastered is True
    assert "Level 3" in msg


def test_suggest_none_proficient_is_lowest():
    results = [_band(1, False), _band(2, False), _band(3, True)]
    major, mastered, msg = suggest_starting_major(results)
    assert major == 1
    assert mastered is False


def test_base_topic_name_strips_level_suffix():
    assert (
        base_topic_name("Arithmetic (Addition) - Level 3")
        == "Arithmetic (Addition)"
    )
    assert (
        base_topic_name("Arithmetic (Addition) – Level 1")
        == "Arithmetic (Addition)"
    )
    assert base_topic_name("Arithmetic (Addition)") == "Arithmetic (Addition)"


def test_major_from_subject_level_suffix():
    assert (
        major_from_subject(
            {"topic": "Arithmetic (Addition) - Level 4", "subject_id": "x"}
        )
        == 4
    )
    assert major_from_subject({"topic": "Arithmetic (Addition)"}) is None
    assert major_from_subject({"subject_id": "math-addition-level-2"}) == 2


def test_sample_questions_balanced_takes_all_when_few():
    qs = [{"question_id": f"q{i}"} for i in range(4)]
    out = sample_questions_balanced(qs, 10)
    assert len(out) == 4
    assert out == qs


def test_sample_questions_balanced_picks_ten_evenly():
    qs = [{"question_id": f"q{i}"} for i in range(100)]
    out = sample_questions_balanced(qs, 10)
    assert len(out) == 10
    ids = [q["question_id"] for q in out]
    assert ids[0] == "q0"
    assert ids[-1] == "q99"
    # evenly spaced, unique
    assert len(set(ids)) == 10


def test_sample_empty():
    assert sample_questions_balanced([], 10) == []


def test_sample_across_sets_spreads_ten_over_many_sets():
    """20 Level N-x sets → 10 questions from 10 different sets (1 each)."""
    banks = []
    for s in range(20):
        lid = f"level-1-{s}"
        qs = [{"question_id": f"{lid}-q{i}", "prompt": f"{s}+{i}"} for i in range(20)]
        banks.append((lid, qs))
    picked = sample_questions_across_sets(banks, 10)
    assert len(picked) == 10
    source_sets = {lid for lid, _q in picked}
    assert len(source_sets) == 10


def test_sample_across_sets_two_sets_split_evenly():
    banks = [
        ("l1-0", [{"question_id": f"a{i}"} for i in range(20)]),
        ("l1-1", [{"question_id": f"b{i}"} for i in range(20)]),
    ]
    picked = sample_questions_across_sets(banks, 10)
    assert len(picked) == 10
    from collections import Counter

    counts = Counter(lid for lid, _ in picked)
    assert counts["l1-0"] == 5
    assert counts["l1-1"] == 5


def test_group_levels_by_major_bands():
    levels = [
        {"level_id": "level-1-0", "name": "Level 1-0", "order": 1},
        {"level_id": "level-1-20", "name": "Level 1-20", "order": 2},
        {"level_id": "level-2-0", "name": "Level 2-0", "order": 3},
        {"level_id": "level-3-5", "name": "Level 3-5", "order": 4},
    ]
    bands = _group_levels_by_major(levels)
    majors = [m for m, _sets in bands]
    assert majors == [1, 2, 3]
    assert len(bands[0][1]) == 2  # Level 1 has two sets
    assert len(bands[1][1]) == 1
    assert len(bands[2][1]) == 1


def test_proficient_requires_superb_or_better():
    assert _is_proficient(
        accuracy=0.9, pass_accuracy=0.8, speed_badge=BADGE_ADVANCED, answered=10
    )
    assert _is_proficient(
        accuracy=1.0, pass_accuracy=0.8, speed_badge=BADGE_LEGENDARY, answered=10
    )
    assert not _is_proficient(
        accuracy=0.9, pass_accuracy=0.8, speed_badge=BADGE_NOVICE, answered=10
    )
    assert not _is_proficient(
        accuracy=0.5, pass_accuracy=0.8, speed_badge=BADGE_LEGENDARY, answered=10
    )
    assert not _is_proficient(
        accuracy=1.0, pass_accuracy=0.8, speed_badge=BADGE_ADVANCED, answered=0
    )
