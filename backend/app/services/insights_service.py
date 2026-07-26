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


def _active_level_ids_for_subjects(subject_ids: set[str]) -> set[tuple[str, str]]:
    """Return {(subject_id, level_id)} for levels that still exist (not soft-deleted)."""
    from app.services import subject_service
    from app.services.subject_service import SubjectNotFound

    active: set[tuple[str, str]] = set()
    for sid in subject_ids:
        if not sid:
            continue
        try:
            for lv in subject_service.list_levels(sid):
                lid = lv.get("level_id")
                if lid:
                    active.add((sid, lid))
        except SubjectNotFound:
            continue
    return active


def learner_insights(user_id: str, subject_id: str | None = None) -> dict[str, Any]:
    """Aggregate progress + recent sessions for dashboard insights.

    Progress and sessions for soft-deleted (or missing) levels are omitted so
    Insights never shows orphaned rows like leftover seed/import levels.
    """
    prefix = "PROGRESS#"
    if subject_id:
        prefix = f"PROGRESS#{subject_id}#"
    progress_items = db.query_pk(keys.user_pk(user_id), sk_begins_with=prefix)
    progress_items = [p for p in progress_items if not p.get("deleted_at")]

    # Drop progress for levels that no longer exist (e.g. admin deleted Level1-0 / l1)
    subject_ids = {
        p.get("subject_id")
        for p in progress_items
        if p.get("subject_id")
    }
    if subject_id:
        subject_ids.add(subject_id)
    active_levels = _active_level_ids_for_subjects(subject_ids)
    progress_items = [
        p
        for p in progress_items
        if (p.get("subject_id"), p.get("level_id")) in active_levels
    ]

    completed = [p for p in progress_items if p.get("status") == "completed"]
    in_progress = [p for p in progress_items if p.get("status") == "in_progress"]
    failed = [p for p in progress_items if p.get("status") == "failed"]

    accuracies = [
        float(p["best_accuracy"])
        for p in progress_items
        if p.get("best_accuracy") is not None
    ]
    avg_accuracy = sum(accuracies) / len(accuracies) if accuracies else None

    # Recent sessions (same active-level filter)
    sessions = db.query_pk(keys.user_pk(user_id), sk_begins_with="SESSION#")
    sessions = [s for s in sessions if not s.get("deleted_at")]
    if subject_id:
        sessions = [s for s in sessions if s.get("subject_id") == subject_id]
    session_subject_ids = {s.get("subject_id") for s in sessions if s.get("subject_id")}
    if session_subject_ids - subject_ids:
        active_levels = active_levels | _active_level_ids_for_subjects(
            session_subject_ids - subject_ids
        )
    sessions = [
        s
        for s in sessions
        if (s.get("subject_id"), s.get("level_id")) in active_levels
    ]
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

    from app.services import study_service, subject_service
    from app.services.study_service import BADGE_LABELS, _better_badge, resolve_progress_badge
    from app.services.subject_service import SubjectNotFound

    content_notices = study_service.list_content_notices(user_id)

    # Index learner progress by (subject_id, level_id)
    progress_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    subject_ids_touched: set[str] = set()
    for p in progress_items:
        sid = p.get("subject_id") or ""
        lid = p.get("level_id") or ""
        if not sid or not lid:
            continue
        progress_by_key[(sid, lid)] = p
        subject_ids_touched.add(sid)

    # Prefer subjects the learner has touched; also include all active subjects
    # so Insights can list full catalogs when exploring STEM content.
    try:
        all_subjects = subject_service.list_subjects()
    except Exception:
        all_subjects = []
    subjects_by_id = {s.get("subject_id"): s for s in all_subjects if s.get("subject_id")}
    for sid in subject_ids_touched:
        if sid not in subjects_by_id:
            try:
                raw = subject_service.get_subject(sid)
                subjects_by_id[sid] = subject_service._public_subject(raw)
            except SubjectNotFound:
                continue

    progress_rows: list[dict[str, Any]] = []
    topic_summary: list[dict[str, Any]] = []

    for sid, subj in sorted(
        subjects_by_id.items(),
        key=lambda kv: (
            (kv[1] or {}).get("sort_order") or 0,
            (kv[1] or {}).get("label") or kv[0] or "",
        ),
    ):
        # Only show topics the learner has started (or all if none? show all with levels)
        try:
            levels = subject_service.list_levels(sid)
        except Exception:
            levels = []
        if not levels:
            continue

        # Skip subjects with zero learner contact unless they have levels and we
        # want full catalog — require at least one progress touch OR include all.
        # Product: show subjects user has progress on; if none at all, empty OK.
        has_any = any((sid, lv.get("level_id")) in progress_by_key for lv in levels)
        if not has_any and subject_ids_touched:
            # User has progress elsewhere; skip untouched subjects
            if sid not in subject_ids_touched:
                continue
        elif not has_any and not subject_ids_touched:
            # No progress yet — still list subjects so table shows all sets as "new"
            pass

        category = (subj or {}).get("category") or "Mathematics"
        topic = (subj or {}).get("topic") or (subj or {}).get("name") or sid
        subject_label = (subj or {}).get("label") or f"{category} - {topic}"

        levels_completed = 0
        elapsed_sum = 0
        elapsed_n = 0
        best_badge = None
        levels_total = len(levels)

        # list_levels already returns levels sorted by admin ORDER field
        for lv in levels:
            lid = lv.get("level_id") or ""
            level_name = lv.get("name") or lid
            try:
                level_order = int(lv.get("order") or 0)
            except (TypeError, ValueError):
                level_order = 0
            p = progress_by_key.get((sid, lid))
            if p:
                badge, badge_label, elapsed_i = resolve_progress_badge(p)
                status = p.get("status") or "in_progress"
                best_accuracy = p.get("best_accuracy")
                if status == "completed":
                    levels_completed += 1
                    if badge:
                        best_badge = _better_badge(best_badge, badge)
                if elapsed_i is not None:
                    elapsed_sum += elapsed_i
                    elapsed_n += 1
            else:
                badge = None
                badge_label = None
                elapsed_i = None
                status = "new"
                best_accuracy = None

            progress_rows.append(
                {
                    "subject_id": sid,
                    "level_id": lid,
                    "level_name": level_name,
                    "order": level_order,
                    "category": category,
                    "topic": topic,
                    "subject_label": subject_label,
                    "status": status,
                    "best_accuracy": best_accuracy,
                    "best_elapsed_ms": elapsed_i,
                    "avg_elapsed_ms": elapsed_i,
                    "speed_badge": badge if status == "completed" else None,
                    "speed_badge_label": badge_label if status == "completed" else None,
                }
            )

        remaining = max(0, levels_total - levels_completed)
        all_complete = levels_total > 0 and remaining == 0
        avg_ms = round(elapsed_sum / elapsed_n, 1) if elapsed_n else None

        # Topic badge only when every question set is completed
        topic_badge = best_badge if all_complete else None
        if remaining == 1:
            encourage = (
                f"Almost there! Just 1 more set to earn your topic badge — you've got this!"
            )
        elif remaining > 1:
            encourage = (
                f"Great progress! Finish {remaining} more set(s) to unlock your topic badge. "
                "Keep going, champion!"
            )
        else:
            encourage = "All sets complete — wear your badge with pride!"

        # Include topic if learner touched it or has any progress system-wide list
        if has_any or not subject_ids_touched or sid in subject_ids_touched:
            topic_summary.append(
                {
                    "subject_id": sid,
                    "category": category,
                    "topic": topic,
                    "subject_label": subject_label,
                    "levels_completed": levels_completed,
                    "levels_total": levels_total,
                    "levels_tracked": levels_total,
                    "levels_remaining": remaining,
                    "all_complete": all_complete,
                    "avg_elapsed_ms": avg_ms,
                    "speed_badge": topic_badge,
                    "speed_badge_label": BADGE_LABELS.get(topic_badge)
                    if topic_badge
                    else None,
                    "encourage_message": encourage if not all_complete else None,
                }
            )

    # If user has progress only on deleted subjects, still surface those rows
    for (sid, lid), p in progress_by_key.items():
        if any(r["subject_id"] == sid and r["level_id"] == lid for r in progress_rows):
            continue
        badge, badge_label, elapsed_i = resolve_progress_badge(p)
        progress_rows.append(
            {
                "subject_id": sid,
                "level_id": lid,
                "level_name": lid,
                "order": 10**9,  # unknown / orphan — after ordered catalog rows
                "category": "—",
                "topic": sid,
                "subject_label": sid,
                "status": p.get("status") or "unknown",
                "best_accuracy": p.get("best_accuracy"),
                "best_elapsed_ms": elapsed_i,
                "avg_elapsed_ms": elapsed_i,
                "speed_badge": badge,
                "speed_badge_label": badge_label,
            }
        )

    # Sort like Admin Content: subject, then level ORDER (not alphabetical level_id)
    progress_rows.sort(
        key=lambda r: (
            r.get("subject_label") or "",
            int(r.get("order") or 0),
            r.get("level_id") or "",
        )
    )
    topic_summary.sort(key=lambda t: t.get("subject_label") or "")

    return {
        "user_id": user_id,
        "subject_id": subject_id,
        "levels_completed": len(completed),
        "levels_in_progress": len(in_progress),
        "levels_failed": len(failed),
        "avg_best_accuracy": round(avg_accuracy, 4) if avg_accuracy is not None else None,
        "avg_session_elapsed_ms": round(avg_session_ms, 1) if avg_session_ms is not None else None,
        "summary": summary,
        "content_notices": content_notices,
        "topic_summary": topic_summary,
        "progress": progress_rows,
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
