"""DynamoDB single-table key helpers.

Entity patterns
---------------
USER#<sub>          / META                      user profile + subscription
USER#<sub>          / TASK#<task_id>            task (owner-scoped)
USER#<sub>          / PROGRESS#<subject>#L#<level>
USER#<sub>          / SESSION#<session_id>      study session
USER#<sub>          / ATTEMPT#<session>#Q#<qid> answer attempt
USER#<sub>          / PAYMENT#<payment_id>      GCash payment record
USER#<sub>          / MASTERY#<id>              mastery topic collection
USER#<sub>          / ACCESS#<subject_id>       topic access/usage (mins, first/last)
SUBJECT#<id>        / META                      subject definition
SUBJECT#<id>        / LEVEL#<level_id>          level definition
SUBJECT#<id>        / LEVEL#<level_id>#Q#<qid>  question
SCHOOL#<id>         / META                      school catalog (admin-managed)

GSI1 (for admin listings / reverse lookups):
  GSI1PK = ENTITY#TASK | ENTITY#SUBJECT | ENTITY#PAYMENT | ENTITY#SCHOOL | ENTITY#LEVEL
           | ENTITY#LEADERBOARD
  GSI1SK = created_at or subject order
  For LEVEL: GSI1SK = <subject_id>#<order zero-padded>#<level_id>
    (list_levels queries GSI1PK=ENTITY#LEVEL, begins_with subject_id# so
     question rows under SK LEVEL#…#Q# are never scanned)
  For LEADERBOARD (USER META): GSI1SK = <inverted_xp:010d>#<user_id>
    (ascending query returns highest XP first for Home top-10)
  For TOPIC ACCESS: GSI1PK = ENTITY#TOPIC_ACCESS#<subject_id>
    GSI1SK = <inverted_total_ms:015d>#<user_id>
    (ascending query returns highest time-on-topic first)
"""

from __future__ import annotations


def user_pk(user_id: str) -> str:
    return f"USER#{user_id}"


def user_meta_sk() -> str:
    return "META"


def school_pk(school_id: str) -> str:
    return f"SCHOOL#{school_id}"


def school_meta_sk() -> str:
    return "META"


def task_sk(task_id: str) -> str:
    return f"TASK#{task_id}"


def progress_sk(subject_id: str, level_id: str) -> str:
    return f"PROGRESS#{subject_id}#L#{level_id}"


def session_sk(session_id: str) -> str:
    return f"SESSION#{session_id}"


def assessment_sk(assessment_id: str) -> str:
    """Placement assessment session (does not write study progress)."""
    return f"ASSESS#{assessment_id}"


def attempt_sk(session_id: str, question_id: str) -> str:
    return f"ATTEMPT#{session_id}#Q#{question_id}"


def payment_sk(payment_id: str) -> str:
    return f"PAYMENT#{payment_id}"


def mastery_sk(mastery_id: str) -> str:
    """Per-user mastery collection under USER#… / MASTERY#…"""
    return f"MASTERY#{mastery_id}"


def access_sk(subject_id: str) -> str:
    """Per-user per-topic access/usage under USER#… / ACCESS#…"""
    return f"ACCESS#{subject_id}"


def subject_pk(subject_id: str) -> str:
    return f"SUBJECT#{subject_id}"


def subject_meta_sk() -> str:
    return "META"


def level_sk(level_id: str) -> str:
    return f"LEVEL#{level_id}"


def question_sk(level_id: str, question_id: str) -> str:
    return f"LEVEL#{level_id}#Q#{question_id}"


# GSI helpers
ENTITY_TASK = "ENTITY#TASK"
ENTITY_SUBJECT = "ENTITY#SUBJECT"
ENTITY_PAYMENT = "ENTITY#PAYMENT"
ENTITY_LEVEL = "ENTITY#LEVEL"
ENTITY_SCHOOL = "ENTITY#SCHOOL"
# Leaderboard: GSI1PK=ENTITY#LEADERBOARD, GSI1SK=<inverted_xp:010d>#<user_id>
# (lower inverted sort key = higher XP so ascending GSI query returns top ranks first)
ENTITY_LEADERBOARD = "ENTITY#LEADERBOARD"
# Admin-shared mastery packs visible to all learners
ENTITY_MASTERY_SHARED = "ENTITY#MASTERY_SHARED"
_LEADERBOARD_XP_PAD = 999_999_999
# 15 digits covers centuries of accumulated milliseconds
_TOPIC_ACCESS_MS_PAD = 10**15


def topic_access_gsi1_pk(subject_id: str) -> str:
    return f"ENTITY#TOPIC_ACCESS#{subject_id}"


def topic_access_gsi1_sk(total_ms: int, user_id: str) -> str:
    """GSI1SK for topic usage: inverted ms so most minutes sort first."""
    inv = _TOPIC_ACCESS_MS_PAD - max(0, int(total_ms or 0))
    return f"{inv:015d}#{user_id}"


def leaderboard_gsi1_sk(xp: int, user_id: str) -> str:
    """GSI1SK for leaderboard: inverted XP so top scores sort first ascending."""
    inv = _LEADERBOARD_XP_PAD - max(0, int(xp or 0))
    return f"{inv:010d}#{user_id}"
