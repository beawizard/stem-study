"""Study sessions, answers, progress, and level unlock."""

from __future__ import annotations

import re
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


# Speed badges from total set completion time (active study ms)
BADGE_LEGENDARY = "legendary_wizard"  # ≤ 30s
BADGE_ADVANCED = "superb_advanced"  # 30s < t < 2 min
BADGE_NOVICE = "cool_novice"  # ≥ 2 min
BADGE_LABELS = {
    BADGE_LEGENDARY: "Legendary Wizard",
    BADGE_ADVANCED: "Superb Advanced",
    BADGE_NOVICE: "Cool Novice",
}
# Rank: higher = better (faster)
_BADGE_RANK = {
    BADGE_NOVICE: 1,
    BADGE_ADVANCED: 2,
    BADGE_LEGENDARY: 3,
}
MS_30S = 30_000
MS_2MIN = 120_000


def speed_badge_for_elapsed_ms(elapsed_ms: int | None) -> str | None:
    """Assign speed badge from set completion duration."""
    if elapsed_ms is None:
        return None
    ms = max(0, int(elapsed_ms))
    if ms <= MS_30S:
        return BADGE_LEGENDARY
    if ms < MS_2MIN:
        return BADGE_ADVANCED
    return BADGE_NOVICE


def _better_badge(a: str | None, b: str | None) -> str | None:
    """Keep the better (faster) of two badge ids."""
    if not a:
        return b
    if not b:
        return a
    return a if _BADGE_RANK.get(a, 0) >= _BADGE_RANK.get(b, 0) else b


# Level IDs/names like Level-1-0, Level 1-20, Level1-3 → major group N, variation x
_LEVEL_NX = re.compile(
    r"(?i)level[\s_-]*(\d+)[\s_.-]+(\d+)"
)
_LEVEL_N = re.compile(r"(?i)level[\s_-]*(\d+)\b")
_L_COMPACT = re.compile(r"(?i)^l(\d+)$")


def parse_level_group(level: dict[str, Any] | str) -> tuple[int | None, int | None]:
    """Parse (major N, variation x) from level id/name in the form Level N-x.

    Returns (None, None) when the naming does not match Level N-x / Level N / lN.
    """
    if isinstance(level, dict):
        texts = [
            str(level.get("level_id") or ""),
            str(level.get("name") or ""),
        ]
    else:
        texts = [str(level or "")]

    for text in texts:
        if not text:
            continue
        m = _LEVEL_NX.search(text)
        if m:
            return int(m.group(1)), int(m.group(2))
        m = _LEVEL_N.search(text)
        if m:
            return int(m.group(1)), 0
        m = _L_COMPACT.match(text.strip())
        if m:
            return int(m.group(1)), 0
    return None, None


def level_major_number(level: dict[str, Any] | str) -> int | None:
    """Major band N for Level N-x (e.g. Level-1-20 → 1)."""
    major, _ = parse_level_group(level)
    return major


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
    """Unlock by major Level N band, not per variation x.

    Naming: ``Level N-x`` (e.g. Level-1-0, Level-1-20) — all variations of N are
    free once Level N is unlocked. Level M (M > N) requires completing **any**
    variation of the previous major band (at least one Level N-* completed).

    Seed-style ids ``l1`` / ``l2`` are treated as majors 1 / 2.
    If names cannot be parsed, falls back to sequential ``order`` unlock.
    """
    levels = subject_service.list_levels(subject_id)
    if not levels:
        raise StudyError("No levels configured for subject")

    target_major = level_major_number(level)
    majors_present = sorted(
        {
            m
            for lv in levels
            if (m := level_major_number(lv)) is not None
        }
    )

    # Prefer Level N-x grouping when both target and catalog parse cleanly
    if target_major is not None and majors_present:
        first_major = majors_present[0]
        if target_major <= first_major:
            return  # lowest major band is always open (any Level 1-x)

        # Immediate previous major that exists in the subject catalog
        prev_majors = [m for m in majors_present if m < target_major]
        if not prev_majors:
            return
        prev_major = max(prev_majors)

        prev_band = [
            lv for lv in levels if level_major_number(lv) == prev_major
        ]
        for lv in prev_band:
            prog = get_progress(user_id, subject_id, lv["level_id"])
            if prog and prog.get("status") == "completed":
                return  # any completed Level N-x unlocks Level N+1 (and beyond in band)

        raise ProgressLocked(
            f"Complete any Level {prev_major} set before attempting Level {target_major} "
            f"('{level.get('level_id')}')"
        )

    # Fallback: legacy sequential unlock by order field
    ordered = sorted(levels, key=lambda lv: lv["order"])
    target_order = level["order"]
    first = ordered[0]
    if level["level_id"] == first["level_id"]:
        return

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

    # Include answers so the client can run offline/local answering, then batch-complete.
    questions = subject_service.list_questions(subject_id, level_id, include_answers=True)
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


def complete_session(
    user_id: str,
    session_id: str,
    *,
    total_elapsed_ms: int,
    answers: list[Any],
) -> dict[str, Any]:
    """
    Grade a full set of client-collected answers and close the session.
    Empty answers are treated as \"0\".
    """
    from app.services import user_service

    session = db.get_item(keys.user_pk(user_id), keys.session_sk(session_id))
    if not session or session.get("deleted_at") or session.get("user_id") != user_id:
        raise StudyError("Session not found")
    if session.get("status") != "in_progress":
        raise StudyError("Session is not in progress")

    qids = list(session.get("question_ids") or [])
    if not qids:
        raise StudyError("Session has no questions")

    # Map submitted answers; missing → "0"
    given: dict[str, str] = {}
    for item in answers:
        if hasattr(item, "question_id"):
            qid = item.question_id
            ans = item.answer if item.answer is not None else "0"
        else:
            qid = item.get("question_id")
            ans = item.get("answer")
        if not qid:
            continue
        text = str(ans if ans is not None else "").strip()
        given[qid] = text if text else "0"

    now = _utcnow_iso()
    correct_count = 0
    details: list[dict[str, Any]] = []

    for qid in qids:
        user_ans = given.get(qid, "0")
        question = subject_service.get_question(
            session["subject_id"],
            session["level_id"],
            qid,
        )
        expected = str(question["answer"]) if question else ""
        correct = bool(question) and _normalize_answer(user_ans) == _normalize_answer(
            expected
        )
        if correct:
            correct_count += 1

        attempt = {
            "PK": keys.user_pk(user_id),
            "SK": keys.attempt_sk(session_id, qid),
            "entity_type": "ATTEMPT",
            "session_id": session_id,
            "question_id": qid,
            "user_id": user_id,
            "subject_id": session["subject_id"],
            "level_id": session["level_id"],
            "given_answer": user_ans,
            "correct": correct,
            "elapsed_ms": 0,
            "created_at": now,
            "deleted_at": "",
        }
        # Idempotent overwrite if re-complete attempted after partial legacy submits
        db.put_item(attempt)
        details.append(
            {
                "question_id": qid,
                "prompt": question.get("prompt") if question else "",
                "given_answer": user_ans,
                "expected_answer": expected,
                "correct": correct,
            }
        )

    total_q = len(qids)
    accuracy = correct_count / total_q if total_q else 0.0
    level = subject_service.get_level(session["subject_id"], session["level_id"])
    pass_accuracy = float(level.get("pass_accuracy", 0.8))
    min_q = int(level.get("min_questions", 5))
    passed = accuracy >= pass_accuracy and total_q >= min_q
    elapsed = max(0, int(total_elapsed_ms))

    session_updates = {
        "status": "completed",
        "completed_at": now,
        "answered": total_q,
        "correct": correct_count,
        "total_elapsed_ms": elapsed,
        "accuracy": accuracy,
        "passed": passed,
        "updated_at": now,
    }
    db.update_item(keys.user_pk(user_id), keys.session_sk(session_id), session_updates)

    progress_status = "completed" if passed else "failed"
    speed_badge = speed_badge_for_elapsed_ms(elapsed) if passed else None
    _upsert_progress(
        user_id,
        session["subject_id"],
        session["level_id"],
        status=progress_status,
        last_session_id=session_id,
        accuracy=accuracy,
        total_elapsed_ms=elapsed,
        correct=correct_count,
        answered=total_q,
        speed_badge=speed_badge,
    )

    user_service.record_study_session(
        user_id,
        elapsed_ms=elapsed,
        accuracy=accuracy,
        passed=passed,
    )

    recommendation = build_recommendation(
        accuracy=accuracy,
        avg_ms_per_question=(elapsed / total_q) if total_q else 0,
        passed=passed,
        subject_id=session["subject_id"],
        level_id=session["level_id"],
        user_id=user_id,
    )

    return {
        "session_id": session_id,
        "subject_id": session["subject_id"],
        "level_id": session["level_id"],
        "session_complete": True,
        "total_questions": total_q,
        "answered": total_q,
        "correct": correct_count,
        "accuracy": accuracy,
        "passed": passed,
        "total_elapsed_ms": elapsed,
        "speed_badge": speed_badge,
        "speed_badge_label": BADGE_LABELS.get(speed_badge) if speed_badge else None,
        "recommendation": recommendation,
        "details": details,
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

    given = (data.answer or "").strip() or "0"
    correct = _normalize_answer(given) == _normalize_answer(str(question["answer"]))
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
        "given_answer": given,
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
        speed_badge = speed_badge_for_elapsed_ms(total_elapsed) if passed else None
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
            speed_badge=speed_badge,
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
                "speed_badge": speed_badge,
                "speed_badge_label": BADGE_LABELS.get(speed_badge) if speed_badge else None,
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
    content_version_seen: str | None = None,
    speed_badge: str | None = None,
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

    # Best (fastest) time + best speed badge earned
    last_elapsed = (
        total_elapsed_ms
        if total_elapsed_ms is not None
        else (existing or {}).get("last_elapsed_ms")
    )
    best_elapsed = last_elapsed
    if existing and existing.get("best_elapsed_ms") is not None:
        prev_e = int(existing["best_elapsed_ms"])
        if best_elapsed is None or prev_e < int(best_elapsed):
            best_elapsed = prev_e
    # Prefer badge from this attempt if passed; never downgrade rank
    prev_badge = (existing or {}).get("speed_badge")
    if speed_badge:
        keep_badge = _better_badge(prev_badge, speed_badge)
    else:
        keep_badge = prev_badge
    # If we improved best time, recompute badge from best time when completed
    if status == "completed" and best_elapsed is not None:
        from_best = speed_badge_for_elapsed_ms(int(best_elapsed))
        keep_badge = _better_badge(keep_badge, from_best)

    # Snapshot of level content version at last scored attempt (for change notices)
    seen_ver = content_version_seen
    if seen_ver is None and status in ("completed", "failed"):
        try:
            level = subject_service.get_level(subject_id, level_id)
            seen_ver = int(level.get("content_version") or 0)
        except Exception:
            seen_ver = 0
    if seen_ver is None and existing and existing.get("content_version_seen") is not None:
        try:
            seen_ver = int(existing.get("content_version_seen"))
        except (TypeError, ValueError):
            seen_ver = 0

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
        "last_elapsed_ms": last_elapsed,
        "best_elapsed_ms": best_elapsed,
        "speed_badge": keep_badge or "",
        "speed_badge_label": BADGE_LABELS.get(keep_badge or "", "") if keep_badge else "",
        "last_correct": correct if correct is not None else (existing or {}).get("last_correct"),
        "last_answered": answered if answered is not None else (existing or {}).get("last_answered"),
        "content_version_seen": int(seen_ver) if seen_ver is not None else (existing or {}).get("content_version_seen"),
        "created_at": (existing or {}).get("created_at") or now,
        "updated_at": now,
        "completed_at": now if status == "completed" else (existing or {}).get("completed_at") or "",
        "deleted_at": "",
    }
    db.put_item(item)
    return item


def resolve_progress_badge(item: dict[str, Any]) -> tuple[str | None, str | None, int | None]:
    """Return (badge_id, badge_label, best_elapsed_ms), deriving badge from time if missing.

    Learners who completed sets before speed badges shipped still get an icon
    from best/last elapsed time.
    """
    elapsed = item.get("best_elapsed_ms")
    if elapsed is None:
        elapsed = item.get("last_elapsed_ms")
    try:
        elapsed_i = int(elapsed) if elapsed is not None else None
    except (TypeError, ValueError):
        elapsed_i = None

    badge = item.get("speed_badge") or None
    if badge == "":
        badge = None
    if not badge and item.get("status") == "completed" and elapsed_i is not None:
        badge = speed_badge_for_elapsed_ms(elapsed_i)
    label = item.get("speed_badge_label") or None
    if label == "":
        label = None
    if badge and not label:
        label = BADGE_LABELS.get(badge)
    return badge, label, elapsed_i


def _public_progress(item: dict[str, Any]) -> dict[str, Any]:
    badge, label, best_elapsed = resolve_progress_badge(item)
    return {
        "subject_id": item.get("subject_id"),
        "level_id": item.get("level_id"),
        "status": item.get("status"),
        "best_accuracy": item.get("best_accuracy"),
        "last_session_id": item.get("last_session_id"),
        "last_elapsed_ms": item.get("last_elapsed_ms"),
        "best_elapsed_ms": best_elapsed
        if best_elapsed is not None
        else item.get("best_elapsed_ms"),
        "speed_badge": badge,
        "speed_badge_label": label,
        "content_version_seen": item.get("content_version_seen"),
        "updated_at": item.get("updated_at"),
        "completed_at": item.get("completed_at") or None,
    }


def list_content_notices(user_id: str) -> list[dict[str, Any]]:
    """Levels the learner practiced whose question bank changed since last attempt.

    Used on Home / Insights / Account to warn about content updates or clears.
    """
    progress_items = db.query_pk(keys.user_pk(user_id), sk_begins_with="PROGRESS#")
    progress_items = [p for p in progress_items if not p.get("deleted_at")]
    notices: list[dict[str, Any]] = []

    for p in progress_items:
        subject_id = p.get("subject_id")
        level_id = p.get("level_id")
        if not subject_id or not level_id:
            continue
        status = p.get("status") or ""
        if status not in ("completed", "failed", "in_progress"):
            continue
        try:
            level = subject_service.get_level(subject_id, level_id)
        except Exception:
            continue

        try:
            current_ver = int(level.get("content_version") or 0)
        except (TypeError, ValueError):
            current_ver = 0
        raw_seen = p.get("content_version_seen")
        try:
            seen_ver = int(raw_seen) if raw_seen is not None and raw_seen != "" else None
        except (TypeError, ValueError):
            # Legacy string timestamps — fall back to time comparison
            seen_ver = None
            content_at = level.get("content_updated_at") or ""
            seen_ts = str(raw_seen)
            if content_at and seen_ts and content_at > seen_ts:
                current_ver = max(current_ver, 1)
                seen_ver = 0

        qcount = int(level.get("question_count") or 0)
        content_at = level.get("content_updated_at") or ""

        if seen_ver is None:
            # Never snapshotted: use progress time vs content_updated_at
            prog_at = p.get("completed_at") or p.get("updated_at") or ""
            if content_at and prog_at and content_at > prog_at:
                changed = True
            elif qcount == 0 and status in ("completed", "failed"):
                changed = True
            else:
                changed = False
        else:
            changed = current_ver > seen_ver
            if not changed and qcount == 0 and status in ("completed", "failed") and current_ver >= seen_ver and current_ver > 0:
                # Cleared in a version bump already captured by current_ver > seen_ver
                changed = current_ver > seen_ver

        if not changed:
            continue

        if qcount == 0:
            change_type = "cleared"
            message = (
                f"Questions for {level_id} were removed since you last practiced. "
                "Your past results are kept, but there is nothing to re-take until new questions are added."
            )
        else:
            change_type = "updated"
            message = (
                f"Questions for {level_id} were updated since you last practiced. "
                "Your past score is still shown; re-take the level to practice the new set."
            )

        subject_label = None
        try:
            subj = subject_service.get_subject(subject_id)
            subject_label = subject_service.subject_label(subj)
        except Exception:
            subject_label = subject_id

        notices.append(
            {
                "subject_id": subject_id,
                "level_id": level_id,
                "level_name": level.get("name") or level_id,
                "subject_label": subject_label,
                "status": status,
                "question_count": qcount,
                "change_type": change_type,
                "content_version": current_ver,
                "content_version_seen": seen_ver,
                "content_updated_at": content_at or None,
                "message": message,
            }
        )

    notices.sort(
        key=lambda n: (n.get("subject_label") or "", n.get("level_id") or "")
    )
    return notices


# ---------------------------------------------------------------------------
# Placement assessment (Home → Assessment)
# ---------------------------------------------------------------------------

# 10 questions per major Level N band (not per Level N-x set).
# Example: Level 1-0 … Level 1-19 + Level 2-… → 10 from Level 1, 10 from Level 2, …
ASSESSMENT_QUESTIONS_PER_LEVEL = 10
# Minimum rank for "proficient" speed (Superb Advanced or Legendary Wizard)
_PROFICIENT_BADGE_MIN_RANK = _BADGE_RANK[BADGE_ADVANCED]

# Subject topics like "Arithmetic (Addition) - Level 3" → base "Arithmetic (Addition)"
_BASE_TOPIC_LEVEL_SUFFIX = re.compile(r"\s*[-–]\s*Level\s+(\d+)\s*$", re.IGNORECASE)
_SUBJECT_LEVEL_TAIL = re.compile(r"(?:^|[\s\-–_])Level\s+(\d+)\s*$", re.IGNORECASE)
_SUBJECT_LEVEL_SLUG = re.compile(r"level[_-]?(\d+)\s*$", re.IGNORECASE)


def base_topic_name(topic_or_name: str | None) -> str:
    """Strip trailing ' - Level N' so Level 1..6 subjects share one topic label."""
    raw = str(topic_or_name or "").strip()
    if not raw:
        return ""
    stripped = _BASE_TOPIC_LEVEL_SUFFIX.sub("", raw).strip()
    return stripped or raw


def major_from_subject(subject: dict[str, Any] | None) -> int | None:
    """Major N from subject topic/name/id, e.g. 'Arithmetic (Addition) - Level 3' → 3."""
    if not subject:
        return None
    for key in ("topic", "name", "subject_id", "label"):
        text = str(subject.get(key) or "")
        if not text:
            continue
        m = _SUBJECT_LEVEL_TAIL.search(text)
        if m:
            return int(m.group(1))
        m = _SUBJECT_LEVEL_SLUG.search(text)
        if m:
            return int(m.group(1))
    return None


def resolve_base_topic_group(subject_id: str) -> tuple[list[dict[str, Any]], str, str]:
    """All subjects in the same base topic as *subject_id*.

    Content is often split as separate subjects per major:
      'Arithmetic (Addition) - Level 1' … 'Arithmetic (Addition) - Level 6'
    Assessment treats those as one topic with six Level bands.
    """
    anchor = subject_service.get_subject(subject_id)
    category = str(anchor.get("category") or "Mathematics")
    base = base_topic_name(anchor.get("topic") or anchor.get("name") or "")
    if not base:
        return [anchor], category, str(anchor.get("topic") or anchor.get("name") or subject_id)

    group: list[dict[str, Any]] = []
    for s in subject_service.list_subjects():
        sc = str(s.get("category") or "Mathematics")
        sb = base_topic_name(s.get("topic") or s.get("name") or "")
        if sc == category and sb == base:
            group.append(s)

    if not group:
        group = [anchor]

    def _sort_key(s: dict[str, Any]) -> tuple:
        maj = major_from_subject(s)
        return (
            maj if maj is not None else 10_000,
            int(s.get("sort_order") or 0),
            str(s.get("subject_id") or ""),
        )

    group.sort(key=_sort_key)
    return group, category, base


def sample_questions_balanced(
    questions: list[dict[str, Any]],
    n: int = ASSESSMENT_QUESTIONS_PER_LEVEL,
) -> list[dict[str, Any]]:
    """Pick up to *n* questions evenly spaced across the bank (stable order)."""
    if not questions:
        return []
    if len(questions) <= n:
        return list(questions)
    # Evenly spaced indices so early/mid/late items are represented
    out: list[dict[str, Any]] = []
    last = -1
    for i in range(n):
        idx = int(round(i * (len(questions) - 1) / (n - 1))) if n > 1 else 0
        if idx <= last:
            idx = min(last + 1, len(questions) - 1)
        last = idx
        out.append(questions[idx])
    return out


def sample_questions_across_sets(
    set_banks: list[tuple[str, list[dict[str, Any]]]],
    n: int = ASSESSMENT_QUESTIONS_PER_LEVEL,
) -> list[tuple[str, dict[str, Any]]]:
    """Pick *n* questions spread across Level N-x sets in one major band.

    Returns list of (bank_key, question). Quotas are split as evenly as
    possible across non-empty sets so many Level N-x banks are represented.
    """
    nonempty = [(lid, qs) for lid, qs in set_banks if qs]
    if not nonempty or n <= 0:
        return []

    k = len(nonempty)
    # Initial equal split (e.g. 20 sets → 10 sets contribute 1 each)
    base = n // k
    rem = n % k
    quotas = [base + (1 if i < rem else 0) for i in range(k)]

    picked: list[tuple[str, dict[str, Any]]] = []
    leftovers: list[tuple[str, list[dict[str, Any]]]] = []

    for i, (lid, qs) in enumerate(nonempty):
        take = min(quotas[i], len(qs))
        chosen = sample_questions_balanced(qs, take)
        chosen_ids = {q.get("question_id") for q in chosen}
        for q in chosen:
            picked.append((lid, q))
        rest = [q for q in qs if q.get("question_id") not in chosen_ids]
        if rest:
            leftovers.append((lid, rest))

    # Top up if some sets had fewer questions than their quota
    if len(picked) < n and leftovers:
        pool: list[tuple[str, dict[str, Any]]] = []
        for lid, qs in leftovers:
            for q in qs:
                pool.append((lid, q))
        need = n - len(picked)
        if pool:
            if len(pool) <= need:
                picked.extend(pool)
            else:
                last = -1
                for i in range(need):
                    idx = int(round(i * (len(pool) - 1) / (need - 1))) if need > 1 else 0
                    if idx <= last:
                        idx = min(last + 1, len(pool) - 1)
                    last = idx
                    picked.append(pool[idx])

    return picked[:n]


def _group_levels_by_major(
    levels: list[dict[str, Any]],
) -> list[tuple[int | None, list[dict[str, Any]]]]:
    """Group Level N-x sets into major bands sorted by N.

    Level 1-0 and Level 1-20 → band 1; Level 3-5 → band 3.
    Sets that cannot be parsed fall into a single fallback band (major=None).
    """
    bands: dict[int, list[dict[str, Any]]] = {}
    unparsed: list[dict[str, Any]] = []
    for lv in levels:
        major = level_major_number(lv)
        if major is None:
            unparsed.append(lv)
        else:
            bands.setdefault(int(major), []).append(lv)

    for major in bands:
        bands[major].sort(
            key=lambda lv: (int(lv.get("order") or 0), str(lv.get("level_id") or ""))
        )

    ordered: list[tuple[int | None, list[dict[str, Any]]]] = [
        (m, bands[m]) for m in sorted(bands.keys())
    ]
    if unparsed:
        unparsed.sort(
            key=lambda lv: (int(lv.get("order") or 0), str(lv.get("level_id") or ""))
        )
        ordered.append((None, unparsed))
    return ordered


def _band_key(major: int | None, index: int) -> str:
    """Stable synthetic id for a major band in assessment meta."""
    if major is not None:
        return f"major-{major}"
    return f"band-{index}"


def _band_display_name(major: int | None, sets: list[dict[str, Any]] | None = None) -> str:
    if major is not None:
        return f"Level {major}"
    sets = sets or []
    if len(sets) == 1:
        return str(sets[0].get("name") or sets[0].get("level_id") or "Level")
    return "Other levels"


def _collect_band_set_meta(
    subjects: list[dict[str, Any]],
) -> dict[int | None, list[dict[str, Any]]]:
    """Gather set metadata per major band — **no question rows loaded**.

    Each entry: {subject_id, level_id, question_count, pass_accuracy, name, order}.
    Uses denormalized ``question_count`` on level META so preview stays fast.
    """
    bands: dict[int | None, list[dict[str, Any]]] = {}

    for subj in subjects:
        sid = str(subj.get("subject_id") or "")
        if not sid:
            continue
        try:
            levels = subject_service.list_levels(sid)
        except Exception:
            continue
        if not levels:
            continue

        subj_major = major_from_subject(subj)

        def _entry(lv: dict[str, Any]) -> dict[str, Any]:
            return {
                "subject_id": sid,
                "level_id": lv["level_id"],
                "name": lv.get("name") or lv["level_id"],
                "order": int(lv.get("order") or 0),
                "question_count": int(lv.get("question_count") or 0),
                "pass_accuracy": float(lv.get("pass_accuracy", 0.8)),
            }

        if subj_major is not None:
            bucket = bands.setdefault(int(subj_major), [])
            for lv in sorted(
                levels,
                key=lambda x: (int(x.get("order") or 0), str(x.get("level_id") or "")),
            ):
                bucket.append(_entry(lv))
        else:
            for major, sets in _group_levels_by_major(levels):
                bucket = bands.setdefault(major, [])
                for lv in sets:
                    bucket.append(_entry(lv))

    return bands


def _sample_from_set_meta(
    set_metas: list[dict[str, Any]],
    n: int = ASSESSMENT_QUESTIONS_PER_LEVEL,
    *,
    include_answers: bool,
) -> list[tuple[str, str, dict[str, Any]]]:
    """Load only sets that receive a quota; return (subject_id, level_id, question).

    With 20 sets and n=10 this does ~10 DynamoDB list_questions calls instead of 20.
    """
    # Prefer sets that report questions; if counts are all zero (stale meta),
    # still try every set so assessment can recover.
    nonempty = [m for m in set_metas if int(m.get("question_count") or 0) > 0]
    candidates = nonempty if nonempty else list(set_metas)
    if not candidates or n <= 0:
        return []

    k = len(candidates)
    base = n // k
    rem = n % k
    quotas = [base + (1 if i < rem else 0) for i in range(k)]

    picked: list[tuple[str, str, dict[str, Any]]] = []
    # Sets that still have unused questions for top-up
    leftover_meta: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []

    for i, meta in enumerate(candidates):
        take = quotas[i]
        if take <= 0:
            continue
        sid = meta["subject_id"]
        lid = meta["level_id"]
        try:
            bank = subject_service.list_questions(
                sid, lid, include_answers=include_answers
            )
        except Exception:
            bank = []
        if not bank:
            continue
        chosen = sample_questions_balanced(bank, min(take, len(bank)))
        chosen_ids = {q.get("question_id") for q in chosen}
        for q in chosen:
            picked.append((sid, lid, q))
        rest = [q for q in bank if q.get("question_id") not in chosen_ids]
        if rest:
            leftover_meta.append((meta, rest))

    if len(picked) < n and leftover_meta:
        pool: list[tuple[str, str, dict[str, Any]]] = []
        for meta, rest in leftover_meta:
            for q in rest:
                pool.append((meta["subject_id"], meta["level_id"], q))
        need = n - len(picked)
        if pool:
            if len(pool) <= need:
                picked.extend(pool)
            else:
                last = -1
                for i in range(need):
                    idx = int(round(i * (len(pool) - 1) / (need - 1))) if need > 1 else 0
                    if idx <= last:
                        idx = min(last + 1, len(pool) - 1)
                    last = idx
                    picked.append(pool[idx])

    # If still short (many empty banks), load additional sets not yet queried
    if len(picked) < n:
        loaded_keys = {(sid, lid) for sid, lid, _q in picked}
        for meta in candidates:
            if len(picked) >= n:
                break
            key = (meta["subject_id"], meta["level_id"])
            if key in loaded_keys:
                continue
            try:
                bank = subject_service.list_questions(
                    meta["subject_id"],
                    meta["level_id"],
                    include_answers=include_answers,
                )
            except Exception:
                continue
            if not bank:
                continue
            loaded_keys.add(key)
            need = n - len(picked)
            for q in sample_questions_balanced(bank, need):
                picked.append((meta["subject_id"], meta["level_id"], q))
                if len(picked) >= n:
                    break

    return picked[:n]


def _build_assessment_bands(
    subject_id: str,
    *,
    include_answers: bool,
    load_questions: bool = True,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, str],
    dict[str, str],
    list[dict[str, Any]],
    str,
    str,
]:
    """Build major-band samples across a full base topic (e.g. all Level 1–6 subjects).

    When ``load_questions`` is False (preview), only level META is read — no
    per-set question scans (avoids Lambda timeouts on large catalogs).

    Returns
    -------
    questions, band_meta, question_level_map, question_subject_map,
    subject_group, category, base_topic
    """
    group, category, base_topic = resolve_base_topic_group(subject_id)
    band_sets = _collect_band_set_meta(group)

    questions: list[dict[str, Any]] = []
    band_meta: list[dict[str, Any]] = []
    question_level_map: dict[str, str] = {}
    question_subject_map: dict[str, str] = {}
    used_qids: set[str] = set()

    def _major_sort(m: int | None) -> tuple:
        return (1, 0) if m is None else (0, int(m))

    for band_index, major in enumerate(sorted(band_sets.keys(), key=_major_sort)):
        set_metas = band_sets[major]
        available = sum(int(m.get("question_count") or 0) for m in set_metas)
        sets_in_band = len(set_metas)
        pass_accs = [float(m.get("pass_accuracy") or 0.8) for m in set_metas]
        pass_acc = sum(pass_accs) / len(pass_accs) if pass_accs else 0.8
        set_ids = [m["level_id"] for m in set_metas]
        subject_ids = list({m["subject_id"] for m in set_metas})
        primary_sid = set_metas[0]["subject_id"] if set_metas else subject_id
        primary_lid = set_metas[0]["level_id"] if set_metas else None

        band_id = _band_key(major, band_index)
        band_name = _band_display_name(major)
        if major is None and base_topic:
            band_name = base_topic

        qids: list[str] = []

        if load_questions:
            sampled = _sample_from_set_meta(
                set_metas,
                ASSESSMENT_QUESTIONS_PER_LEVEL,
                include_answers=include_answers,
            )
            if not sampled:
                # Start path needs real questions; skip empty bands
                if include_answers:
                    continue
            for source_sid, source_lid, q in sampled:
                raw_qid = str(q.get("question_id") or "")
                if not raw_qid:
                    continue
                qid = raw_qid
                if qid in used_qids:
                    qid = f"{source_sid}#{source_lid}#{raw_qid}"
                used_qids.add(qid)
                qids.append(qid)
                question_level_map[qid] = source_lid
                question_subject_map[qid] = source_sid
                row: dict[str, Any] = {
                    "question_id": qid,
                    "source_question_id": raw_qid,
                    "prompt": q.get("prompt"),
                    "level_id": source_lid,
                    "band_id": band_id,
                    "subject_id": source_sid,
                    "level_name": band_name,
                    "major": major,
                }
                if include_answers:
                    row["answer"] = q.get("answer")
                questions.append(row)
            sample_size = len(qids)
            if sample_size == 0 and include_answers:
                continue
        else:
            # Preview: estimate sample without loading banks
            sample_size = min(ASSESSMENT_QUESTIONS_PER_LEVEL, available) if available else 0
            if sample_size == 0 and sets_in_band == 0:
                continue
            # If question_count is missing/stale (0) but sets exist, still advertise
            # up to 10 so Start Assessment will attempt a real sample.
            if sample_size == 0 and sets_in_band > 0:
                sample_size = ASSESSMENT_QUESTIONS_PER_LEVEL

        band_meta.append(
            {
                "level_id": band_id,
                "name": band_name,
                "major": major,
                "order": major if major is not None else band_index,
                "pass_accuracy": pass_acc,
                "question_ids": qids,
                "sample_size": sample_size,
                "available": available,
                "sets_in_band": sets_in_band,
                "set_ids": set_ids,
                "subject_ids": subject_ids,
                "primary_subject_id": primary_sid,
                "primary_level_id": primary_lid,
            }
        )

    return (
        questions,
        band_meta,
        question_level_map,
        question_subject_map,
        group,
        category,
        base_topic,
    )


def preview_assessment(subject_id: str) -> dict[str, Any]:
    """How many major bands / questions an assessment would include for a base topic.

    Fast path: only list_levels (META) — does not scan question banks.
    """
    (
        _questions,
        band_meta,
        _lmap,
        _smap,
        group,
        category,
        base_topic,
    ) = _build_assessment_bands(
        subject_id, include_answers=False, load_questions=False
    )
    total = sum(int(b.get("sample_size") or 0) for b in band_meta)
    level_previews = [
        {
            "level_id": b["level_id"],
            "name": b["name"],
            "order": b.get("order", 0),
            "major": b.get("major"),
            "available": b.get("available", 0),
            "sample_size": b.get("sample_size", 0),
            "sets_in_band": b.get("sets_in_band", 0),
        }
        for b in band_meta
    ]
    subject_ids = [str(s.get("subject_id") or "") for s in group if s.get("subject_id")]
    return {
        "subject_id": subject_id,
        "subject_ids": subject_ids,
        "subject_label": f"{category} - {base_topic}" if base_topic else category,
        "category": category,
        "topic": base_topic,
        "base_topic": base_topic,
        "questions_per_level": ASSESSMENT_QUESTIONS_PER_LEVEL,
        "level_count": len(level_previews),
        "major_count": len(level_previews),
        "total_questions": total,
        "levels": level_previews,
    }


def start_assessment(user_id: str, subject_id: str) -> dict[str, Any]:
    """Build a placement quiz: 10 questions per major Level N across a base topic.

    Subjects named ``Arithmetic (Addition) - Level 1`` … ``Level 6`` are treated
    as one topic → typically 60 questions (10 × 6 majors).

    Loads only the sets that receive a sample quota (not every set in the bank).
    Does not check unlocks and does not write study progress.
    """
    (
        questions,
        level_meta,
        question_level_map,
        question_subject_map,
        group,
        category,
        base_topic,
    ) = _build_assessment_bands(
        subject_id, include_answers=True, load_questions=True
    )
    if not questions:
        raise StudyError("No questions available for assessment in this topic")

    assessment_id = _new_id()
    now = _utcnow_iso()
    subject_ids = [str(s.get("subject_id") or "") for s in group if s.get("subject_id")]
    # Anchor subject for Study deep-links (prefer lowest major)
    anchor_id = subject_ids[0] if subject_ids else subject_id

    item = {
        "PK": keys.user_pk(user_id),
        "SK": keys.assessment_sk(assessment_id),
        "entity_type": "ASSESSMENT",
        "assessment_id": assessment_id,
        "session_id": assessment_id,  # client reuses study session field
        "user_id": user_id,
        "subject_id": anchor_id,
        "subject_ids": subject_ids,
        "category": category,
        "base_topic": base_topic,
        "status": "in_progress",
        "started_at": now,
        "completed_at": "",
        "total_questions": len(questions),
        "question_ids": [q["question_id"] for q in questions],
        "question_level_map": question_level_map,
        "question_subject_map": question_subject_map,
        "levels": level_meta,
        "created_at": now,
        "updated_at": now,
        "deleted_at": "",
    }
    db.put_item(item)

    return {
        "session_id": assessment_id,
        "assessment_id": assessment_id,
        "is_assessment": True,
        "subject_id": anchor_id,
        "subject_ids": subject_ids,
        "subject_label": f"{category} - {base_topic}" if base_topic else category,
        "category": category,
        "base_topic": base_topic,
        "level_id": "assessment",
        "levels": level_meta,
        "questions": questions,
        "started_at": now,
        "total_questions": len(questions),
        "questions_per_level": ASSESSMENT_QUESTIONS_PER_LEVEL,
        "pass_accuracy": 0.8,
        "min_questions": 1,
    }


def _badge_rank(badge: str | None) -> int:
    if not badge:
        return 0
    return int(_BADGE_RANK.get(badge, 0))


def _is_proficient(
    *,
    accuracy: float,
    pass_accuracy: float,
    speed_badge: str | None,
    answered: int,
) -> bool:
    """Proficient = pass accuracy and at least Superb Advanced speed."""
    if answered <= 0:
        return False
    if accuracy < pass_accuracy:
        return False
    return _badge_rank(speed_badge) >= _PROFICIENT_BADGE_MIN_RANK


def suggest_starting_major(
    level_results: list[dict[str, Any]],
) -> tuple[int | None, bool, str]:
    """Choose placement start level from per-band assessment results.

    Rule (gap-aware):
      - Suggest the **lowest** non-proficient major band (failed / needs practice).
      - If every band is proficient, suggest the highest major (mastered).
      - If no majors parse, return (None, False, …).

    Example: proficient on 1,2,3,6 but practice on 4,5 → suggest Level **4**.
    """
    ordered = sorted(
        [lr for lr in level_results if lr.get("major") is not None],
        key=lambda lr: int(lr["major"]),
    )
    if not ordered:
        return None, False, (
            "Start with the first level in this topic and build speed and accuracy."
        )

    all_majors = [int(lr["major"]) for lr in ordered]
    failed = [int(lr["major"]) for lr in ordered if not lr.get("proficient")]
    proficient_majors = [int(lr["major"]) for lr in ordered if lr.get("proficient")]

    if failed:
        suggested = min(failed)
        # How far they cleared before the first gap
        cleared = [m for m in proficient_majors if m < suggested]
        if cleared:
            msg = (
                f"You did well through Level {max(cleared)}, but Level {suggested} "
                f"needs more practice. Suggested starting point: Level {suggested}."
            )
        else:
            msg = (
                f"Level {suggested} needs more practice. "
                f"Suggested starting point: Level {suggested}."
            )
        return suggested, False, msg

    # All assessed majors proficient
    highest = max(all_majors)
    msg = (
        f"Amazing! You're proficient through Level {highest}. "
        f"You can study any set — try Level {highest} for a challenge."
    )
    return highest, True, msg


def complete_assessment(
    user_id: str,
    assessment_id: str,
    *,
    total_elapsed_ms: int,
    answers: list[Any],
) -> dict[str, Any]:
    """Grade placement assessment and suggest a starting Level N."""
    item = db.get_item(keys.user_pk(user_id), keys.assessment_sk(assessment_id))
    if not item or item.get("deleted_at") or item.get("user_id") != user_id:
        raise StudyError("Assessment not found")
    if item.get("status") != "in_progress":
        raise StudyError("Assessment is not in progress")

    qids = list(item.get("question_ids") or [])
    if not qids:
        raise StudyError("Assessment has no questions")

    # source set (subject_id, level_id) per question for Dynamo answer lookup
    q_level_map: dict[str, str] = dict(item.get("question_level_map") or {})
    q_subject_map: dict[str, str] = dict(item.get("question_subject_map") or {})
    # levels_meta is one row per major band (Level 1, Level 2, …)
    levels_meta: list[dict[str, Any]] = list(item.get("levels") or [])
    band_by_id = {lv["level_id"]: lv for lv in levels_meta}

    def _band_for_question(qid: str) -> dict[str, Any] | None:
        for band in levels_meta:
            if qid in (band.get("question_ids") or []):
                return band
        return None

    given: dict[str, str] = {}
    for ans in answers:
        if hasattr(ans, "question_id"):
            qid = ans.question_id
            text = ans.answer if ans.answer is not None else "0"
        else:
            qid = ans.get("question_id")
            text = ans.get("answer")
        if not qid:
            continue
        s = str(text if text is not None else "").strip()
        given[qid] = s if s else "0"

    subject_id = item["subject_id"]
    now = _utcnow_iso()
    details: list[dict[str, Any]] = []
    # Accumulate per major band (not per Level N-x set)
    per_band: dict[str, dict[str, Any]] = {
        bid: {
            "level_id": bid,
            "name": (band_by_id.get(bid) or {}).get("name") or bid,
            "major": (band_by_id.get(bid) or {}).get("major"),
            "pass_accuracy": float(
                (band_by_id.get(bid) or {}).get("pass_accuracy") or 0.8
            ),
            "correct": 0,
            "answered": 0,
            "question_ids": list((band_by_id.get(bid) or {}).get("question_ids") or []),
            "primary_level_id": (band_by_id.get(bid) or {}).get("primary_level_id"),
            "primary_subject_id": (band_by_id.get(bid) or {}).get("primary_subject_id"),
        }
        for bid in band_by_id
    }

    total_correct = 0
    for qid in qids:
        band = _band_for_question(qid)
        source_lid = q_level_map.get(qid)
        source_sid = q_subject_map.get(qid) or subject_id
        if not source_lid and band:
            # Fallback: first set in band
            sets = band.get("set_ids") or []
            source_lid = sets[0] if sets else None
            if band.get("primary_subject_id"):
                source_sid = band["primary_subject_id"]
        if not source_lid:
            continue
        # Composite assessment ids: subject#level#raw_qid
        lookup_qid = qid
        if "#" in qid and qid.count("#") >= 2:
            lookup_qid = qid.split("#", 2)[-1]
        band_id = (band or {}).get("level_id")
        user_ans = given.get(qid, "0")
        question = subject_service.get_question(source_sid, source_lid, lookup_qid)
        expected = str(question["answer"]) if question else ""
        correct = bool(question) and _normalize_answer(user_ans) == _normalize_answer(
            expected
        )
        if correct:
            total_correct += 1
            if band_id and band_id in per_band:
                per_band[band_id]["correct"] += 1
        if band_id and band_id in per_band:
            per_band[band_id]["answered"] += 1
        details.append(
            {
                "question_id": qid,
                "level_id": source_lid,
                "subject_id": source_sid,
                "band_id": band_id,
                "major": (band or {}).get("major"),
                "prompt": question.get("prompt") if question else "",
                "given_answer": user_ans,
                "expected_answer": expected,
                "correct": correct,
            }
        )

    elapsed = max(0, int(total_elapsed_ms))
    total_q = len(qids)
    overall_accuracy = total_correct / total_q if total_q else 0.0

    # Allocate elapsed time proportionally by question count per major band
    level_results: list[dict[str, Any]] = []
    for band in levels_meta:
        bid = band["level_id"]
        row = per_band.get(bid) or {
            "level_id": bid,
            "name": band.get("name") or bid,
            "major": band.get("major"),
            "pass_accuracy": float(band.get("pass_accuracy") or 0.8),
            "correct": 0,
            "answered": 0,
            "primary_level_id": band.get("primary_level_id"),
        }
        answered = int(row["answered"])
        correct_c = int(row["correct"])
        accuracy = correct_c / answered if answered else 0.0
        share = (answered / total_q) if total_q else 0.0
        level_elapsed = int(round(elapsed * share))
        badge = speed_badge_for_elapsed_ms(level_elapsed) if answered else None
        pass_acc = float(row.get("pass_accuracy") or 0.8)
        proficient = _is_proficient(
            accuracy=accuracy,
            pass_accuracy=pass_acc,
            speed_badge=badge,
            answered=answered,
        )
        level_results.append(
            {
                "level_id": bid,
                "name": row.get("name") or bid,
                "major": row.get("major"),
                "answered": answered,
                "correct": correct_c,
                "accuracy": round(accuracy, 4),
                "pass_accuracy": pass_acc,
                "passed": accuracy >= pass_acc and answered > 0,
                "elapsed_ms": level_elapsed,
                "speed_badge": badge if accuracy >= pass_acc else None,
                "speed_badge_label": (
                    BADGE_LABELS.get(badge)
                    if badge and accuracy >= pass_acc
                    else None
                ),
                "proficient": proficient,
                "primary_level_id": row.get("primary_level_id")
                or band.get("primary_level_id"),
                "primary_subject_id": row.get("primary_subject_id")
                or band.get("primary_subject_id"),
            }
        )

    # major_results mirrors level_results (already one row per major band)
    major_results: list[dict[str, Any]] = []
    proficient_majors: list[int] = []
    for lr in level_results:
        major = lr.get("major")
        if major is None:
            continue
        m = int(major)
        if lr.get("proficient"):
            proficient_majors.append(m)
        major_results.append(
            {
                "major": m,
                "answered": lr["answered"],
                "correct": lr["correct"],
                "accuracy": lr["accuracy"],
                "elapsed_ms": lr["elapsed_ms"],
                "speed_badge": lr.get("speed_badge"),
                "speed_badge_label": lr.get("speed_badge_label"),
                "proficient": lr["proficient"],
                "sets": 1,
                "proficient_sets": 1 if lr["proficient"] else 0,
            }
        )

    suggested_major, mastered, suggestion_message = suggest_starting_major(
        level_results
    )

    # Map suggested major → subject + first set in that band (Study deep-link)
    suggested_level_id = None
    suggested_level_name = None
    suggested_subject_id = item.get("subject_id")
    if suggested_major is not None:
        for band in levels_meta:
            if band.get("major") == suggested_major:
                suggested_level_id = band.get("primary_level_id") or (
                    (band.get("set_ids") or [None])[0]
                )
                suggested_subject_id = (
                    band.get("primary_subject_id")
                    or ((band.get("subject_ids") or [None])[0])
                    or suggested_subject_id
                )
                suggested_level_name = band.get("name") or f"Level {suggested_major}"
                break
    if not suggested_level_id and levels_meta:
        # Prefer lowest non-proficient band; else first band
        first_non = next((lr for lr in level_results if not lr.get("proficient")), None)
        pick = first_non or level_results[0]
        meta = band_by_id.get(pick["level_id"]) or {}
        suggested_level_id = (
            pick.get("primary_level_id")
            or meta.get("primary_level_id")
            or (meta.get("set_ids") or [None])[0]
            or pick["level_id"]
        )
        suggested_subject_id = (
            pick.get("primary_subject_id")
            or meta.get("primary_subject_id")
            or suggested_subject_id
        )
        suggested_level_name = pick.get("name") or suggested_level_id
        if suggested_major is None and pick.get("major") is not None:
            suggested_major = int(pick["major"])

    db.update_item(
        keys.user_pk(user_id),
        keys.assessment_sk(assessment_id),
        {
            "status": "completed",
            "completed_at": now,
            "total_elapsed_ms": elapsed,
            "correct": total_correct,
            "answered": total_q,
            "accuracy": overall_accuracy,
            "suggested_major": suggested_major,
            "suggested_level_id": suggested_level_id,
            "suggested_subject_id": suggested_subject_id,
            "updated_at": now,
        },
    )

    base_topic = item.get("base_topic") or ""
    category = item.get("category") or "Mathematics"
    if base_topic:
        subject_label = f"{category} - {base_topic}"
    else:
        try:
            subject = subject_service.get_subject(subject_id)
            subject_label = subject_service.subject_label(subject)
        except Exception:
            subject_label = subject_id

    return {
        "session_id": assessment_id,
        "assessment_id": assessment_id,
        "is_assessment": True,
        "session_complete": True,
        "subject_id": suggested_subject_id or subject_id,
        "subject_label": subject_label,
        "base_topic": base_topic,
        "category": category,
        "total_questions": total_q,
        "answered": total_q,
        "correct": total_correct,
        "accuracy": round(overall_accuracy, 4),
        "total_elapsed_ms": elapsed,
        "level_results": level_results,
        "major_results": major_results,
        "proficient_majors": proficient_majors,
        "suggested_major": suggested_major,
        "suggested_level_id": suggested_level_id,
        "suggested_level_name": suggested_level_name,
        "mastered_topic": mastered,
        "suggestion_message": suggestion_message,
        "details": details,
    }
