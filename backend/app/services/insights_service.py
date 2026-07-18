"""Learner insights and study recommendations."""

from __future__ import annotations

from typing import Any

from app import db, keys


# Thresholds (ms per question)
FAST_MS = 5_000
SLOW_MS = 30_000
HIGH_ACCURACY = 0.9
LOW_ACCURACY = 0.6


def build_recommendation(
    *,
    accuracy: float,
    avg_ms_per_question: float,
    passed: bool,
    subject_id: str,
    level_id: str,
    user_id: str,
) -> dict[str, Any]:
    """Rule-based recommendation from session metrics."""
    tags: list[str] = []
    actions: list[str] = []

    if accuracy >= HIGH_ACCURACY and avg_ms_per_question <= FAST_MS:
        tags.append("mastery")
        actions.append("You are fast and accurate — advance to the next level when ready.")
    elif accuracy >= HIGH_ACCURACY and avg_ms_per_question > SLOW_MS:
        tags.append("careful_mastery")
        actions.append(
            "Great accuracy, but take timed drills to improve speed without sacrificing correctness."
        )
    elif accuracy < LOW_ACCURACY:
        tags.append("needs_review")
        actions.append(
            f"Review fundamentals for {subject_id}/{level_id}. Re-attempt after short practice."
        )
        if avg_ms_per_question <= FAST_MS:
            tags.append("rushed")
            actions.append("Slow down — quick answers with low accuracy suggest rushing.")
    elif not passed:
        tags.append("almost_there")
        actions.append(
            f"Close to passing. Aim for higher accuracy on {level_id} before moving on."
        )
    else:
        tags.append("solid")
        actions.append("Solid performance. Continue to the next level or reinforce with a retake.")

    if avg_ms_per_question > SLOW_MS and accuracy >= LOW_ACCURACY:
        tags.append("slow")
        actions.append("Practice shorter mixed sets to build fluency.")

    return {
        "accuracy": round(accuracy, 4),
        "avg_ms_per_question": round(avg_ms_per_question, 1),
        "passed": passed,
        "tags": tags,
        "actions": actions,
        "subject_id": subject_id,
        "level_id": level_id,
    }


def learner_insights(user_id: str, subject_id: str | None = None) -> dict[str, Any]:
    """Aggregate progress + recent sessions for dashboard insights."""
    prefix = "PROGRESS#"
    if subject_id:
        prefix = f"PROGRESS#{subject_id}#"
    progress_items = db.query_pk(keys.user_pk(user_id), sk_begins_with=prefix)
    progress_items = [p for p in progress_items if not p.get("deleted_at")]

    completed = [p for p in progress_items if p.get("status") == "completed"]
    in_progress = [p for p in progress_items if p.get("status") == "in_progress"]
    failed = [p for p in progress_items if p.get("status") == "failed"]

    accuracies = [
        float(p["best_accuracy"])
        for p in progress_items
        if p.get("best_accuracy") is not None
    ]
    avg_accuracy = sum(accuracies) / len(accuracies) if accuracies else None

    # Recent sessions
    sessions = db.query_pk(keys.user_pk(user_id), sk_begins_with="SESSION#")
    sessions = [s for s in sessions if not s.get("deleted_at")]
    if subject_id:
        sessions = [s for s in sessions if s.get("subject_id") == subject_id]
    sessions.sort(key=lambda s: s.get("started_at") or "", reverse=True)
    recent = sessions[:10]

    avg_session_ms = None
    completed_sessions = [s for s in recent if s.get("status") == "completed"]
    if completed_sessions:
        avg_session_ms = sum(int(s.get("total_elapsed_ms") or 0) for s in completed_sessions) / len(
            completed_sessions
        )

    # High-level recommendation
    if failed and not completed:
        summary = "Focus on foundation levels before advancing."
    elif in_progress and not failed:
        summary = "Keep going — finish your in-progress levels."
    elif completed and avg_accuracy is not None and avg_accuracy >= HIGH_ACCURACY:
        summary = "Strong mastery overall. Try the next challenge level."
    elif avg_accuracy is not None and avg_accuracy < LOW_ACCURACY:
        summary = "Accuracy is below target. Revisit failed levels with slower, careful practice."
    else:
        summary = "Steady progress. Complete remaining levels and watch for speed vs accuracy balance."

    return {
        "user_id": user_id,
        "subject_id": subject_id,
        "levels_completed": len(completed),
        "levels_in_progress": len(in_progress),
        "levels_failed": len(failed),
        "avg_best_accuracy": round(avg_accuracy, 4) if avg_accuracy is not None else None,
        "avg_session_elapsed_ms": round(avg_session_ms, 1) if avg_session_ms is not None else None,
        "summary": summary,
        "progress": [
            {
                "subject_id": p.get("subject_id"),
                "level_id": p.get("level_id"),
                "status": p.get("status"),
                "best_accuracy": p.get("best_accuracy"),
            }
            for p in progress_items
        ],
        "recent_sessions": [
            {
                "session_id": s.get("session_id"),
                "subject_id": s.get("subject_id"),
                "level_id": s.get("level_id"),
                "status": s.get("status"),
                "accuracy": s.get("accuracy"),
                "total_elapsed_ms": s.get("total_elapsed_ms"),
                "passed": s.get("passed"),
                "started_at": s.get("started_at"),
            }
            for s in recent
        ],
    }
