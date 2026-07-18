"""Study sessions, answers, progress, and level unlock."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app import db, keys
from app.services import subject_service
from app.services.insights_service import build_recommendation
from app.validation import AnswerSubmit


class StudyError(Exception):
    pass


class ProgressLocked(StudyError):
    """User has not unlocked this level yet."""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return uuid.uuid4().hex


def _normalize_answer(ans: str) -> str:
    return " ".join(ans.strip().lower().split())


def get_progress(user_id: str, subject_id: str, level_id: str) -> dict[str, Any] | None:
    item = db.get_item(
        keys.user_pk(user_id),
        keys.progress_sk(subject_id, level_id),
    )
    if not item or item.get("deleted_at"):
        return None
    return item


def list_progress(user_id: str, subject_id: str | None = None) -> list[dict[str, Any]]:
    prefix = f"PROGRESS#{subject_id}#" if subject_id else "PROGRESS#"
    items = db.query_pk(keys.user_pk(user_id), sk_begins_with=prefix)
    return [_public_progress(i) for i in items if not i.get("deleted_at")]


def _ensure_level_unlocked(user_id: str, subject_id: str, level: dict[str, Any]) -> None:
    """First level (lowest order) is always unlocked; others require prior level completed."""
    levels = subject_service.list_levels(subject_id)
    if not levels:
        raise StudyError("No levels configured for subject")

    ordered = sorted(levels, key=lambda lv: lv["order"])
    target_order = level["order"]
    first = ordered[0]
    if level["level_id"] == first["level_id"]:
        return

    # Find previous level by order
    prev = None
    for lv in ordered:
        if lv["order"] < target_order:
            prev = lv
        elif lv["level_id"] == level["level_id"]:
            break

    if prev is None:
        return

    prev_progress = get_progress(user_id, subject_id, prev["level_id"])
    if not prev_progress or prev_progress.get("status") != "completed":
        raise ProgressLocked(
            f"Complete level '{prev['level_id']}' before attempting '{level['level_id']}'"
        )


def start_session(user_id: str, subject_id: str, level_id: str) -> dict[str, Any]:
    level = subject_service.get_level(subject_id, level_id)
    _ensure_level_unlocked(user_id, subject_id, level)

    questions = subject_service.list_questions(subject_id, level_id, include_answers=False)
    if not questions:
        raise StudyError("No questions available for this level")

    session_id = _new_id()
    now = _utcnow_iso()
    item = {
        "PK": keys.user_pk(user_id),
        "SK": keys.session_sk(session_id),
        "entity_type": "SESSION",
        "session_id": session_id,
        "user_id": user_id,
        "subject_id": subject_id,
        "level_id": level_id,
        "status": "in_progress",
        "started_at": now,
        "completed_at": "",
        "total_questions": len(questions),
        "answered": 0,
        "correct": 0,
        "total_elapsed_ms": 0,
        "question_ids": [q["question_id"] for q in questions],
        "created_at": now,
        "updated_at": now,
        "deleted_at": "",
    }
    db.put_item(item)

    # Mark progress in_progress if not completed
    _upsert_progress(
        user_id,
        subject_id,
        level_id,
        status="in_progress",
        last_session_id=session_id,
    )

    return {
        "session_id": session_id,
        "subject_id": subject_id,
        "level_id": level_id,
        "questions": questions,
        "started_at": now,
        "total_questions": len(questions),
        "pass_accuracy": float(level.get("pass_accuracy", 0.8)),
        "min_questions": int(level.get("min_questions", 5)),
    }


def submit_answer(
    user_id: str,
    session_id: str,
    data: AnswerSubmit,
) -> dict[str, Any]:
    session = db.get_item(keys.user_pk(user_id), keys.session_sk(session_id))
    if not session or session.get("deleted_at") or session.get("user_id") != user_id:
        raise StudyError("Session not found")
    if session.get("status") != "in_progress":
        raise StudyError("Session is not in progress")

    qids = session.get("question_ids") or []
    if data.question_id not in qids:
        raise StudyError("Question not part of this session")

    # Prevent double-answer
    existing_attempt = db.get_item(
        keys.user_pk(user_id),
        keys.attempt_sk(session_id, data.question_id),
    )
    if existing_attempt:
        raise StudyError("Question already answered in this session")

    question = subject_service.get_question(
        session["subject_id"],
        session["level_id"],
        data.question_id,
    )
    if not question:
        raise StudyError("Question not found")

    correct = _normalize_answer(data.answer) == _normalize_answer(str(question["answer"]))
    now = _utcnow_iso()
    attempt = {
        "PK": keys.user_pk(user_id),
        "SK": keys.attempt_sk(session_id, data.question_id),
        "entity_type": "ATTEMPT",
        "session_id": session_id,
        "question_id": data.question_id,
        "user_id": user_id,
        "subject_id": session["subject_id"],
        "level_id": session["level_id"],
        "given_answer": data.answer,
        "correct": correct,
        "elapsed_ms": data.elapsed_ms,
        "created_at": now,
        "deleted_at": "",
    }
    db.put_item(attempt)

    answered = int(session.get("answered") or 0) + 1
    correct_count = int(session.get("correct") or 0) + (1 if correct else 0)
    total_elapsed = int(session.get("total_elapsed_ms") or 0) + data.elapsed_ms
    total_q = int(session.get("total_questions") or len(qids))

    session_updates: dict[str, Any] = {
        "answered": answered,
        "correct": correct_count,
        "total_elapsed_ms": total_elapsed,
        "updated_at": now,
    }

    finished = answered >= total_q
    result: dict[str, Any] = {
        "question_id": data.question_id,
        "correct": correct,
        "expected_answer": question["answer"] if not correct else None,
        "answered": answered,
        "total_questions": total_q,
        "session_complete": finished,
    }

    if finished:
        accuracy = correct_count / total_q if total_q else 0.0
        level = subject_service.get_level(session["subject_id"], session["level_id"])
        pass_accuracy = float(level.get("pass_accuracy", 0.8))
        min_q = int(level.get("min_questions", 5))
        passed = accuracy >= pass_accuracy and total_q >= min_q

        session_updates["status"] = "completed"
        session_updates["completed_at"] = now
        session_updates["accuracy"] = accuracy
        session_updates["passed"] = passed

        progress_status = "completed" if passed else "failed"
        _upsert_progress(
            user_id,
            session["subject_id"],
            session["level_id"],
            status=progress_status,
            last_session_id=session_id,
            accuracy=accuracy,
            total_elapsed_ms=total_elapsed,
            correct=correct_count,
            answered=answered,
        )

        recommendation = build_recommendation(
            accuracy=accuracy,
            avg_ms_per_question=(total_elapsed / total_q) if total_q else 0,
            passed=passed,
            subject_id=session["subject_id"],
            level_id=session["level_id"],
            user_id=user_id,
        )
        result.update(
            {
                "accuracy": accuracy,
                "passed": passed,
                "total_elapsed_ms": total_elapsed,
                "recommendation": recommendation,
            }
        )

    db.update_item(keys.user_pk(user_id), keys.session_sk(session_id), session_updates)
    return result


def get_session(user_id: str, session_id: str) -> dict[str, Any]:
    session = db.get_item(keys.user_pk(user_id), keys.session_sk(session_id))
    if not session or session.get("deleted_at") or session.get("user_id") != user_id:
        raise StudyError("Session not found")
    return {
        "session_id": session.get("session_id"),
        "subject_id": session.get("subject_id"),
        "level_id": session.get("level_id"),
        "status": session.get("status"),
        "started_at": session.get("started_at"),
        "completed_at": session.get("completed_at") or None,
        "total_questions": session.get("total_questions"),
        "answered": session.get("answered"),
        "correct": session.get("correct"),
        "total_elapsed_ms": session.get("total_elapsed_ms"),
        "accuracy": session.get("accuracy"),
        "passed": session.get("passed"),
    }


def _upsert_progress(
    user_id: str,
    subject_id: str,
    level_id: str,
    *,
    status: str,
    last_session_id: str,
    accuracy: float | None = None,
    total_elapsed_ms: int | None = None,
    correct: int | None = None,
    answered: int | None = None,
) -> dict[str, Any]:
    now = _utcnow_iso()
    pk = keys.user_pk(user_id)
    sk = keys.progress_sk(subject_id, level_id)
    existing = db.get_item(pk, sk)

    # Do not downgrade completed -> failed/in_progress
    if existing and existing.get("status") == "completed" and status != "completed":
        status = "completed"
        accuracy = existing.get("best_accuracy", accuracy)

    best_accuracy = accuracy
    if existing and existing.get("best_accuracy") is not None:
        prev = float(existing["best_accuracy"])
        if best_accuracy is None or prev > best_accuracy:
            best_accuracy = prev

    item = {
        "PK": pk,
        "SK": sk,
        "entity_type": "PROGRESS",
        "user_id": user_id,
        "subject_id": subject_id,
        "level_id": level_id,
        "status": status,
        "last_session_id": last_session_id,
        "best_accuracy": best_accuracy if best_accuracy is not None else existing.get("best_accuracy") if existing else None,
        "last_elapsed_ms": total_elapsed_ms if total_elapsed_ms is not None else (existing or {}).get("last_elapsed_ms"),
        "last_correct": correct if correct is not None else (existing or {}).get("last_correct"),
        "last_answered": answered if answered is not None else (existing or {}).get("last_answered"),
        "created_at": (existing or {}).get("created_at") or now,
        "updated_at": now,
        "completed_at": now if status == "completed" else (existing or {}).get("completed_at") or "",
        "deleted_at": "",
    }
    db.put_item(item)
    return item


def _public_progress(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "subject_id": item.get("subject_id"),
        "level_id": item.get("level_id"),
        "status": item.get("status"),
        "best_accuracy": item.get("best_accuracy"),
        "last_session_id": item.get("last_session_id"),
        "last_elapsed_ms": item.get("last_elapsed_ms"),
        "updated_at": item.get("updated_at"),
        "completed_at": item.get("completed_at") or None,
    }
