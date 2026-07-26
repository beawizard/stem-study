"""Learner insights and study recommendations."""

from __future__ import annotations

from typing import Any

from app import db, keys


# Thresholds (ms per question)
FAST_MS = 5_000
SLOW_MS = 30_000
HIGH_ACCURACY = 0.9
LOW_ACCURACY = 0.6

# I3: bound SESSION# reads (SK is random uuid — we sample a cap, then sort by started_at)
RECENT_SESSIONS_LIMIT = 10
SESSION_SCAN_CAP = 40


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


def _levels_by_subject(subject_ids: set[str]) -> dict[str, list[dict[str, Any]]]:
    """I4: list_levels once per subject (GSI META only)."""
    from app.services import subject_service
    from app.services.subject_service import SubjectNotFound

    out: dict[str, list[dict[str, Any]]] = {}
    for sid in subject_ids:
        if not sid:
            continue
        try:
            out[sid] = subject_service.list_levels(sid)
        except SubjectNotFound:
            out[sid] = []
        except Exception:
            out[sid] = []
    return out


def _active_level_ids_from_map(
    levels_by_subject: dict[str, list[dict[str, Any]]],
) -> set[tuple[str, str]]:
    active: set[tuple[str, str]] = set()
    for sid, levels in levels_by_subject.items():
        for lv in levels:
            lid = lv.get("level_id")
            if lid:
                active.add((sid, lid))
    return active


def learner_insights(
    user_id: str,
    subject_id: str | None = None,
    *,
    include_notices: bool = False,
) -> dict[str, Any]:
    """Aggregate progress + recent sessions for dashboard insights.

    Performance (I1–I4):
      I1 — only subjects the learner has touched (no full catalog expand)
      I2 — content_notices optional (default off; use GET /me?notices=1)
      I3 — cap SESSION# reads (no full session history)
      I4 — list_levels once per touched subject; reuse for filter + table
    """
    from app.services import study_service, subject_service
    from app.services.study_service import BADGE_LABELS, _better_badge, resolve_progress_badge
    from app.services.subject_service import SubjectNotFound

    prefix = "PROGRESS#"
    if subject_id:
        prefix = f"PROGRESS#{subject_id}#"
    progress_items = db.query_pk(keys.user_pk(user_id), sk_begins_with=prefix)
    progress_items = [p for p in progress_items if not p.get("deleted_at")]

    # Subjects the learner has actually practiced
    subject_ids_touched: set[str] = {
        str(p.get("subject_id"))
        for p in progress_items
        if p.get("subject_id")
    }
    if subject_id:
        subject_ids_touched.add(subject_id)

    # I4: one list_levels pass for touched subjects only
    levels_by_subject = _levels_by_subject(subject_ids_touched)
    active_levels = _active_level_ids_from_map(levels_by_subject)

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

    # I3: bound session reads (avoid loading entire SESSION# history)
    sessions = db.query_pk(
        keys.user_pk(user_id),
        sk_begins_with="SESSION#",
        limit=SESSION_SCAN_CAP,
        scan_forward=False,
    )
    sessions = [s for s in sessions if not s.get("deleted_at")]
    if subject_id:
        sessions = [s for s in sessions if s.get("subject_id") == subject_id]
    # Prefer sessions whose levels still exist
    sessions = [
        s
        for s in sessions
        if not s.get("subject_id")
        or not s.get("level_id")
        or (s.get("subject_id"), s.get("level_id")) in active_levels
        or (s.get("subject_id") not in levels_by_subject)
    ]
    sessions.sort(key=lambda s: s.get("started_at") or "", reverse=True)
    recent = sessions[:RECENT_SESSIONS_LIMIT]

    avg_session_ms = None
    completed_sessions = [s for s in recent if s.get("status") == "completed"]
    if completed_sessions:
        avg_session_ms = sum(
            int(s.get("total_elapsed_ms") or 0) for s in completed_sessions
        ) / len(completed_sessions)
    elif completed:
        # Fallback: mean of best/last elapsed on completed progress rows
        elapsed_vals = []
        for p in completed:
            v = p.get("best_elapsed_ms")
            if v is None:
                v = p.get("last_elapsed_ms")
            if v is not None:
                try:
                    elapsed_vals.append(int(v))
                except (TypeError, ValueError):
                    pass
        if elapsed_vals:
            avg_session_ms = sum(elapsed_vals) / len(elapsed_vals)

    # High-level recommendation
    if not progress_items:
        summary = "Start studying to see your insights and progress here."
    elif failed and not completed:
        summary = "Focus on foundation levels before advancing."
    elif in_progress and not failed:
        summary = "Keep going — finish your in-progress levels."
    elif completed and avg_accuracy is not None and avg_accuracy >= HIGH_ACCURACY:
        summary = "Strong mastery overall. Try the next challenge level."
    elif avg_accuracy is not None and avg_accuracy < LOW_ACCURACY:
        summary = "Accuracy is below target. Revisit failed levels with slower, careful practice."
    else:
        summary = "Steady progress. Complete remaining levels and watch for speed vs accuracy balance."

    # I2: skip expensive list_content_notices unless explicitly requested
    content_notices: list[dict[str, Any]] = []
    if include_notices:
        content_notices = study_service.list_content_notices(user_id)

    # Index learner progress by (subject_id, level_id)
    progress_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for p in progress_items:
        sid = p.get("subject_id") or ""
        lid = p.get("level_id") or ""
        if not sid or not lid:
            continue
        progress_by_key[(sid, lid)] = p

    # I1: only subjects the learner has touched — no full catalog expand
    subjects_by_id: dict[str, dict[str, Any]] = {}
    for sid in subject_ids_touched:
        try:
            raw = subject_service.get_subject(sid)
            subjects_by_id[sid] = subject_service._public_subject(raw)
        except SubjectNotFound:
            continue
        except Exception:
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
        # I1: skip subjects with no progress touch
        has_any = any(k[0] == sid for k in progress_by_key)
        if not has_any:
            continue

        # I4: reuse levels from the single pass above
        levels = levels_by_subject.get(sid) or []
        if not levels:
            continue

        category = (subj or {}).get("category") or "Mathematics"
        topic = (subj or {}).get("topic") or (subj or {}).get("name") or sid
        subject_label = (subj or {}).get("label") or f"{category} - {topic}"

        levels_completed = 0
        elapsed_sum = 0
        elapsed_n = 0
        best_badge = None
        levels_total = len(levels)

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

        topic_badge = best_badge if all_complete else None
        if remaining == 1:
            encourage = (
                "Almost there! Just 1 more set to earn your topic badge — you've got this!"
            )
        elif remaining > 1:
            encourage = (
                f"Great progress! Finish {remaining} more set(s) to unlock your topic badge. "
                "Keep going, champion!"
            )
        else:
            encourage = "All sets complete — wear your badge with pride!"

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
                "speed_badge_label": BADGE_LABELS.get(topic_badge) if topic_badge else None,
                "encourage_message": encourage if not all_complete else None,
            }
        )

    # Progress on deleted subjects/levels (orphan rows) — surface without re-querying catalog
    for (sid, lid), p in progress_by_key.items():
        if any(r["subject_id"] == sid and r["level_id"] == lid for r in progress_rows):
            continue
        badge, badge_label, elapsed_i = resolve_progress_badge(p)
        progress_rows.append(
            {
                "subject_id": sid,
                "level_id": lid,
                "level_name": lid,
                "order": 10**9,
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
        "avg_session_elapsed_ms": round(avg_session_ms, 1)
        if avg_session_ms is not None
        else None,
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
