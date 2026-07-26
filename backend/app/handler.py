"""API Gateway HTTP API Lambda entrypoint – single function router.

Routes (all require Cognito JWT unless noted):
  GET    /health
  GET|PATCH         /me
  GET               /schools                     (public catalog for sign-up)
  POST              /schools                     (admin)
  PUT|DELETE        /schools/{id}                (admin)
  GET|POST          /tasks
  GET|PUT|DELETE    /tasks/{task_id}
  GET               /subjects
  POST              /subjects                    (admin)
  PUT               /subjects/{id}               (admin)  update topic/description/category
  POST              /subjects/{id}/levels        (admin)
  GET               /subjects/{id}/levels
  PUT|DELETE        /subjects/{id}/levels/{lid}  (admin)
  POST              /subjects/{id}/levels/{lid}/questions  (admin, CSV body; ?replace=true)
  GET|DELETE        /subjects/{id}/levels/{lid}/questions  (DELETE clears set, admin)
  PUT|DELETE        /subjects/{id}/levels/{lid}/questions/{qid}  (admin)
  POST              /study/sessions
  POST              /study/sessions/{id}/answers
  POST              /study/sessions/{id}/complete   (batch complete after client-side quiz)
  GET               /study/sessions/{id}
  GET               /study/assessment/preview?subject_id=
  POST              /study/assessment              (start placement assessment)
  POST              /study/assessment/{id}/complete
  GET               /study/progress
  GET               /insights
  POST              /payments
  GET               /payments
  GET               /admin/payments              (admin)
  POST              /admin/payments/{user_id}/{payment_id}/verify  (admin)
  POST              /admin/seed                  (admin)
"""

from __future__ import annotations

import base64
import logging
import re
from typing import Any, Callable
from urllib.parse import unquote

from app.auth import AuthError, UserContext, get_user_context, require_admin
from app.response import (
    bad_request,
    created,
    forbidden,
    no_content,
    not_found,
    ok,
    server_error,
    unauthorized,
    unprocessable,
)
from app.services import (
    insights_service,
    payment_service,
    school_service,
    study_service,
    subject_service,
    task_service,
    user_service,
)
from app.services.payment_service import PaymentError, PaymentNotFound
from app.services.school_service import SchoolConflict, SchoolNotFound
from app.services.study_service import ProgressLocked, StudyError
from app.services.subject_service import (
    ConflictError,
    LevelNotFound,
    QuestionNotFound,
    SubjectNotFound,
)
from app.services.task_service import TaskNotFound
from app.validation import (
    AnswerSubmit,
    LevelCreate,
    LevelUpdate,
    PaymentSubmit,
    PaymentVerify,
    ProfileUpdate,
    QuestionUpdate,
    SchoolCreate,
    SchoolUpdate,
    SessionComplete,
    StartAssessment,
    StartSession,
    SubjectCreate,
    SubjectUpdate,
    TaskCreate,
    TaskUpdate,
    parse_body,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# path param patterns
_TASK_ID = re.compile(r"^/tasks/([^/]+)$")
_SUBJECT_ID = re.compile(r"^/subjects/([^/]+)$")
_SUBJECT_LEVELS = re.compile(r"^/subjects/([^/]+)/levels$")
_SUBJECT_LEVEL = re.compile(r"^/subjects/([^/]+)/levels/([^/]+)$")
_SUBJECT_LEVEL_QUESTIONS = re.compile(r"^/subjects/([^/]+)/levels/([^/]+)/questions$")
_SUBJECT_LEVEL_QUESTION = re.compile(
    r"^/subjects/([^/]+)/levels/([^/]+)/questions/([^/]+)$"
)
_SESSION = re.compile(r"^/study/sessions/([^/]+)$")
_SESSION_ANSWERS = re.compile(r"^/study/sessions/([^/]+)/answers$")
_SESSION_COMPLETE = re.compile(r"^/study/sessions/([^/]+)/complete$")
_ASSESSMENT_COMPLETE = re.compile(r"^/study/assessment/([^/]+)/complete$")
_ADMIN_VERIFY = re.compile(r"^/admin/payments/([^/]+)/([^/]+)/verify$")
_SCHOOL_ID = re.compile(r"^/schools/([^/]+)$")


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Lambda handler."""
    try:
        return _dispatch(event)
    except AuthError as exc:
        if exc.status == 403:
            return forbidden(exc.message)
        return unauthorized(exc.message)
    except ValueError as exc:
        return unprocessable(str(exc))
    except Exception:
        logger.exception("Unhandled error")
        return server_error()


def _dispatch(event: dict[str, Any]) -> dict[str, Any]:
    method = (
        event.get("requestContext", {}).get("http", {}).get("method")
        or event.get("httpMethod")
        or "GET"
    ).upper()
    path = event.get("rawPath") or event.get("path") or "/"
    # Normalize trailing slash (except root)
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    if method == "OPTIONS":
        return ok({"ok": True})

    if method == "GET" and path == "/health":
        return ok({"status": "healthy", "service": "stem-study"})

    # Public school catalog (sign-up combobox; no JWT required)
    if method == "GET" and path == "/schools":
        return ok({"schools": school_service.list_schools()})

    user = get_user_context(event)

    # Ensure profile exists (free access for now — no subscription gate)
    profile = payment_service.refresh_subscription_status(user.user_id)
    # Attach email / nickname from token on first create or when missing
    profile = user_service.ensure_user_profile(
        user.user_id,
        email=user.email,
        nickname=user.nickname,
    )

    return _route(method, path, event, user, profile)


def _route(
    method: str,
    path: str,
    event: dict[str, Any],
    user: UserContext,
    profile: dict[str, Any],
) -> dict[str, Any]:
    body = _body(event)
    qs = event.get("queryStringParameters") or {}

    # --- Me ---
    if method == "GET" and path == "/me":
        return ok(user_service.public_profile(profile))

    if method == "PATCH" and path == "/me":
        data = parse_body(ProfileUpdate, body)
        updated = user_service.update_profile(
            user.user_id,
            nickname=data.nickname,
            school_id=data.school_id,
            grade=data.grade,
        )
        return ok(user_service.public_profile(updated))

    # --- Schools (admin write) ---
    if method == "POST" and path == "/schools":
        require_admin(user)
        data = parse_body(SchoolCreate, body)
        try:
            return created(
                school_service.create_school(
                    name=data.name,
                    city=data.city or "",
                    province=data.province or "",
                    school_id=data.school_id,
                )
            )
        except SchoolConflict as exc:
            return bad_request(str(exc))

    m_school = _SCHOOL_ID.match(path)
    if m_school:
        school_id = unquote(m_school.group(1))
        if method == "PUT" or method == "PATCH":
            require_admin(user)
            data = parse_body(SchoolUpdate, body)
            try:
                return ok(
                    school_service.update_school(
                        school_id,
                        name=data.name,
                        city=data.city,
                        province=data.province,
                    )
                )
            except SchoolNotFound:
                return not_found("School not found")
        if method == "DELETE":
            require_admin(user)
            try:
                school_service.soft_delete_school(school_id)
                return no_content()
            except SchoolNotFound:
                return not_found("School not found")

    # --- Tasks ---
    if method == "GET" and path == "/tasks":
        include = qs.get("include_completed", "true").lower() != "false"
        return ok({"tasks": task_service.list_tasks(user.user_id, include_completed=include)})

    if method == "POST" and path == "/tasks":
        data = parse_body(TaskCreate, body)
        return created(task_service.create_task(user.user_id, data))  # type: ignore[arg-type]

    m = _TASK_ID.match(path)
    if m:
        task_id = unquote(m.group(1))
        if method == "GET":
            try:
                return ok(task_service.get_task(user.user_id, task_id))
            except TaskNotFound:
                return not_found("Task not found")
        if method == "PUT" or method == "PATCH":
            try:
                data = parse_body(TaskUpdate, body)
                return ok(task_service.update_task(user.user_id, task_id, data))  # type: ignore[arg-type]
            except TaskNotFound:
                return not_found("Task not found")
        if method == "DELETE":
            try:
                task_service.soft_delete_task(user.user_id, task_id)
                return no_content()
            except TaskNotFound:
                return not_found("Task not found")

    # --- Subjects ---
    if method == "GET" and path == "/subjects":
        return ok({"subjects": subject_service.list_subjects()})

    if method == "POST" and path == "/subjects":
        require_admin(user)
        data = parse_body(SubjectCreate, body)
        try:
            return created(subject_service.create_subject(data))  # type: ignore[arg-type]
        except ConflictError as exc:
            return bad_request(str(exc))

    # More specific /subjects/{id}/levels* routes match first (below).
    # PUT /subjects/{id} updates topic/description/category for working subject.
    m = _SUBJECT_ID.match(path)
    if m and method in ("PUT", "PATCH"):
        require_admin(user)
        subject_id = unquote(m.group(1))
        data = parse_body(SubjectUpdate, body)
        try:
            return ok(subject_service.update_subject(subject_id, data))  # type: ignore[arg-type]
        except SubjectNotFound:
            return not_found("Subject not found")
        except ValueError as exc:
            return bad_request(str(exc))

    m = _SUBJECT_LEVELS.match(path)
    if m:
        subject_id = unquote(m.group(1))
        if method == "GET":
            try:
                return ok(
                    {
                        "subject_id": subject_id,
                        "levels": subject_service.list_levels(subject_id),
                    }
                )
            except SubjectNotFound:
                return not_found("Subject not found")
        if method == "POST":
            require_admin(user)
            data = parse_body(LevelCreate, body)
            try:
                return created(subject_service.create_level(subject_id, data))  # type: ignore[arg-type]
            except SubjectNotFound:
                return not_found("Subject not found")
            except ConflictError as exc:
                return bad_request(str(exc))

    m = _SUBJECT_LEVEL.match(path)
    if m:
        subject_id, level_id = unquote(m.group(1)), unquote(m.group(2))
        if method == "PUT" or method == "PATCH":
            require_admin(user)
            data = parse_body(LevelUpdate, body)
            try:
                return ok(
                    subject_service.update_level(subject_id, level_id, data)  # type: ignore[arg-type]
                )
            except (SubjectNotFound, LevelNotFound):
                return not_found("Subject or level not found")
        if method == "DELETE":
            require_admin(user)
            try:
                return ok(subject_service.soft_delete_level(subject_id, level_id))
            except (SubjectNotFound, LevelNotFound):
                return not_found("Subject or level not found")

    m = _SUBJECT_LEVEL_QUESTION.match(path)
    if m:
        subject_id, level_id, question_id = (
            unquote(m.group(1)),
            unquote(m.group(2)),
            unquote(m.group(3)),
        )
        if method == "PUT" or method == "PATCH":
            require_admin(user)
            data = parse_body(QuestionUpdate, body)
            try:
                return ok(
                    subject_service.update_question(
                        subject_id, level_id, question_id, data  # type: ignore[arg-type]
                    )
                )
            except (SubjectNotFound, LevelNotFound):
                return not_found("Subject or level not found")
            except QuestionNotFound:
                return not_found("Question not found")
        if method == "DELETE":
            require_admin(user)
            try:
                subject_service.soft_delete_question(subject_id, level_id, question_id)
                return no_content()
            except (SubjectNotFound, LevelNotFound):
                return not_found("Subject or level not found")
            except QuestionNotFound:
                return not_found("Question not found")

    m = _SUBJECT_LEVEL_QUESTIONS.match(path)
    if m:
        subject_id, level_id = unquote(m.group(1)), unquote(m.group(2))
        if method == "GET":
            try:
                include_answers = user.is_admin and qs.get("include_answers") == "true"
                return ok(
                    {
                        "questions": subject_service.list_questions(
                            subject_id, level_id, include_answers=include_answers
                        )
                    }
                )
            except (SubjectNotFound, LevelNotFound):
                return not_found("Subject or level not found")
        if method == "POST":
            require_admin(user)
            # CSV as raw body or JSON {"csv": "..."}; ?replace=true clears first
            csv_text = _extract_csv(body, event)
            replace = (qs.get("replace") or "").lower() in ("1", "true", "yes")
            try:
                summary = subject_service.import_questions_csv(
                    subject_id, level_id, csv_text, replace=replace
                )
                return created(summary)
            except (SubjectNotFound, LevelNotFound):
                return not_found("Subject or level not found")
            except ValueError as exc:
                return unprocessable(str(exc))
        if method == "DELETE":
            require_admin(user)
            try:
                return ok(subject_service.clear_questions(subject_id, level_id))
            except (SubjectNotFound, LevelNotFound):
                return not_found("Subject or level not found")

    # --- Study ---
    if method == "POST" and path == "/study/sessions":
        data = parse_body(StartSession, body)
        try:
            return created(
                study_service.start_session(user.user_id, data.subject_id, data.level_id)  # type: ignore[attr-defined]
            )
        except ProgressLocked as exc:
            return forbidden(str(exc))
        except (SubjectNotFound, LevelNotFound):
            return not_found("Subject or level not found")
        except StudyError as exc:
            return bad_request(str(exc))

    m = _SESSION_ANSWERS.match(path)
    if m:
        session_id = unquote(m.group(1))
        if method == "POST":
            data = parse_body(AnswerSubmit, body)
            try:
                return ok(study_service.submit_answer(user.user_id, session_id, data))  # type: ignore[arg-type]
            except StudyError as exc:
                return bad_request(str(exc))

    m = _SESSION_COMPLETE.match(path)
    if m:
        session_id = unquote(m.group(1))
        if method == "POST":
            data = parse_body(SessionComplete, body)
            try:
                return ok(
                    study_service.complete_session(
                        user.user_id,
                        session_id,
                        total_elapsed_ms=data.total_elapsed_ms,  # type: ignore[attr-defined]
                        answers=data.answers,  # type: ignore[attr-defined]
                    )
                )
            except StudyError as exc:
                return bad_request(str(exc))

    m = _SESSION.match(path)
    if m:
        session_id = unquote(m.group(1))
        if method == "GET":
            try:
                return ok(study_service.get_session(user.user_id, session_id))
            except StudyError:
                return not_found("Session not found")

    if method == "GET" and path == "/study/progress":
        subject_id = qs.get("subject_id")
        return ok({"progress": study_service.list_progress(user.user_id, subject_id)})

    # --- Placement assessment (Home → Assessment) ---
    if method == "GET" and path == "/study/assessment/preview":
        subject_id = qs.get("subject_id")
        if not subject_id:
            return bad_request("subject_id is required")
        try:
            return ok(study_service.preview_assessment(subject_id))
        except SubjectNotFound:
            return not_found("Subject not found")
        except StudyError as exc:
            return bad_request(str(exc))

    if method == "POST" and path == "/study/assessment":
        data = parse_body(StartAssessment, body)
        try:
            return created(
                study_service.start_assessment(user.user_id, data.subject_id)  # type: ignore[attr-defined]
            )
        except SubjectNotFound:
            return not_found("Subject not found")
        except StudyError as exc:
            return bad_request(str(exc))

    m = _ASSESSMENT_COMPLETE.match(path)
    if m:
        assessment_id = unquote(m.group(1))
        if method == "POST":
            data = parse_body(SessionComplete, body)
            try:
                return ok(
                    study_service.complete_assessment(
                        user.user_id,
                        assessment_id,
                        total_elapsed_ms=data.total_elapsed_ms,  # type: ignore[attr-defined]
                        answers=data.answers,  # type: ignore[attr-defined]
                    )
                )
            except StudyError as exc:
                return bad_request(str(exc))

    if method == "GET" and path == "/insights":
        subject_id = qs.get("subject_id")
        return ok(insights_service.learner_insights(user.user_id, subject_id))

    # --- Payments ---
    if method == "POST" and path == "/payments":
        data = parse_body(PaymentSubmit, body)
        return created(payment_service.submit_payment(user.user_id, data))  # type: ignore[arg-type]

    if method == "GET" and path == "/payments":
        return ok({"payments": payment_service.list_user_payments(user.user_id)})

    if method == "GET" and path == "/admin/payments":
        require_admin(user)
        return ok({"payments": payment_service.list_pending_payments()})

    m = _ADMIN_VERIFY.match(path)
    if m and method == "POST":
        require_admin(user)
        pay_user_id, payment_id = unquote(m.group(1)), unquote(m.group(2))
        data = parse_body(PaymentVerify, body)
        try:
            return ok(
                payment_service.verify_payment(
                    pay_user_id,
                    payment_id,
                    status=data.status,  # type: ignore[attr-defined]
                    admin_user_id=user.user_id,
                    notes=data.notes or "",  # type: ignore[attr-defined]
                )
            )
        except PaymentNotFound:
            return not_found("Payment not found")
        except PaymentError as exc:
            return bad_request(str(exc))

    if method == "POST" and path == "/admin/seed":
        require_admin(user)
        return ok(subject_service.seed_math_defaults())

    return not_found(f"No route for {method} {path}")


def _body(event: dict[str, Any]) -> str | None:
    body = event.get("body")
    if body is None:
        return None
    if event.get("isBase64Encoded"):
        return base64.b64decode(body).decode("utf-8")
    return body


def _extract_csv(body: str | None, event: dict[str, Any]) -> str:
    import json

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    ctype = headers.get("content-type", "")
    if "text/csv" in ctype or "text/plain" in ctype:
        if not body:
            raise ValueError("Empty CSV body")
        return body
    if body:
        try:
            data = json.loads(body)
            if isinstance(data, dict) and "csv" in data:
                return str(data["csv"])
        except json.JSONDecodeError:
            # treat raw body as CSV
            return body
    raise ValueError("Provide CSV as text/csv body or JSON {\"csv\": \"...\"}")
