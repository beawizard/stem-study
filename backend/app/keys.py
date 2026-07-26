"""DynamoDB single-table key helpers.

Entity patterns
---------------
USER#<sub>          / META                      user profile + subscription
USER#<sub>          / TASK#<task_id>            task (owner-scoped)
USER#<sub>          / PROGRESS#<subject>#L#<level>
USER#<sub>          / SESSION#<session_id>      study session
USER#<sub>          / ATTEMPT#<session>#Q#<qid> answer attempt
USER#<sub>          / PAYMENT#<payment_id>      GCash payment record
SUBJECT#<id>        / META                      subject definition
SUBJECT#<id>        / LEVEL#<level_id>          level definition
SUBJECT#<id>        / LEVEL#<level_id>#Q#<qid>  question
SCHOOL#<id>         / META                      school catalog (admin-managed)

GSI1 (for admin listings / reverse lookups):
  GSI1PK = ENTITY#TASK | ENTITY#SUBJECT | ENTITY#PAYMENT | ENTITY#SCHOOL
  GSI1SK = created_at or subject order
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
