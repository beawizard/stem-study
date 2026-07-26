"""Input validation utilities."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict


# Shared constraints
MAX_TITLE_LEN = 200
MAX_DESC_LEN = 2000
MAX_SUBJECT_ID_LEN = 64
MAX_LEVEL_ID_LEN = 64
MAX_CSV_ROWS = 5000
ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
SUBJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")

# STEM subject categories (admin Content creation)
STEM_CATEGORIES = ("Science", "Technology", "Engineering", "Mathematics")


class ProfileUpdate(BaseModel):
    """PATCH /me — learner display name, school, grade (all optional; at least one)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    nickname: str | None = Field(default=None, min_length=1, max_length=40)
    school_id: str | None = Field(default=None, max_length=64)
    grade: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def at_least_one_field(self) -> "ProfileUpdate":
        if self.nickname is None and self.school_id is None and self.grade is None:
            raise ValueError("Provide at least one of: nickname, school_id, grade")
        return self


class SchoolCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(..., min_length=1, max_length=120)
    city: str = Field(default="", max_length=80)
    province: str = Field(default="", max_length=80)
    school_id: str | None = Field(default=None, max_length=64)


class SchoolUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    city: str | None = Field(default=None, max_length=80)
    province: str | None = Field(default=None, max_length=80)


class TaskCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str = Field(..., min_length=1, max_length=MAX_TITLE_LEN)
    description: str = Field(default="", max_length=MAX_DESC_LEN)
    due_date: str | None = Field(default=None, max_length=32)
    subject_id: str | None = Field(default=None, max_length=MAX_SUBJECT_ID_LEN)

    @field_validator("subject_id")
    @classmethod
    def validate_subject_id(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not SUBJECT_ID_PATTERN.match(v):
            raise ValueError("Invalid subject_id format")
        return v


class TaskUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=MAX_TITLE_LEN)
    description: str | None = Field(default=None, max_length=MAX_DESC_LEN)
    due_date: str | None = Field(default=None, max_length=32)
    completed: bool | None = None
    subject_id: str | None = Field(default=None, max_length=MAX_SUBJECT_ID_LEN)

    @field_validator("subject_id")
    @classmethod
    def validate_subject_id(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not SUBJECT_ID_PATTERN.match(v):
            raise ValueError("Invalid subject_id format")
        return v


class SubjectCreate(BaseModel):
    """Create a subject (topic) under a STEM category.

    Preferred fields: category + topic. subject_id is optional (auto-slugged).
    Legacy: name maps to topic; subject_id may still be supplied explicitly.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    category: str = Field(default="Mathematics", min_length=1, max_length=32)
    topic: str | None = Field(default=None, min_length=1, max_length=100)
    # Legacy alias for topic (kept so existing clients/tests keep working)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    subject_id: str | None = Field(default=None, min_length=1, max_length=MAX_SUBJECT_ID_LEN)
    description: str = Field(default="", max_length=MAX_DESC_LEN)
    sort_order: int = Field(default=0, ge=0, le=10000)

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        # Accept exact STEM labels; also allow case-insensitive match
        for cat in STEM_CATEGORIES:
            if v.lower() == cat.lower():
                return cat
        raise ValueError(
            f"category must be one of: {', '.join(STEM_CATEGORIES)}"
        )

    @field_validator("subject_id")
    @classmethod
    def validate_subject_id(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not SUBJECT_ID_PATTERN.match(v):
            raise ValueError(
                "subject_id must be lowercase alphanumeric/underscore/hyphen, start with letter"
            )
        return v

    @model_validator(mode="after")
    def require_topic_or_name(self) -> "SubjectCreate":
        if not ((self.topic and self.topic.strip()) or (self.name and self.name.strip())):
            raise ValueError("topic is required (or provide name for legacy clients)")
        return self

    def resolved_topic(self) -> str:
        """Topic from explicit topic or legacy name field."""
        return (self.topic or self.name or "").strip()


class SubjectUpdate(BaseModel):
    """Partial update of a subject (topic, description, category, sort_order)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    category: str | None = Field(default=None, min_length=1, max_length=32)
    topic: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=MAX_DESC_LEN)
    sort_order: int | None = Field(default=None, ge=0, le=10000)

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        for cat in STEM_CATEGORIES:
            if v.lower() == cat.lower():
                return cat
        raise ValueError(f"category must be one of: {', '.join(STEM_CATEGORIES)}")


class LevelCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    level_id: str = Field(..., min_length=1, max_length=MAX_LEVEL_ID_LEN)
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=MAX_DESC_LEN)
    order: int = Field(..., ge=1, le=1000)
    pass_accuracy: float = Field(default=0.8, ge=0.0, le=1.0)
    min_questions: int = Field(default=5, ge=1, le=500)

    @field_validator("level_id")
    @classmethod
    def validate_level_id(cls, v: str) -> str:
        if not ID_PATTERN.match(v):
            raise ValueError("Invalid level_id format")
        return v


class LevelUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=MAX_DESC_LEN)
    order: int | None = Field(default=None, ge=1, le=1000)
    pass_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    min_questions: int | None = Field(default=None, ge=1, le=500)


class QuestionUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    prompt: str | None = Field(default=None, min_length=1, max_length=500)
    answer: str | None = Field(default=None, min_length=1, max_length=200)


class AnswerSubmit(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    question_id: str = Field(..., min_length=1, max_length=64)
    answer: str = Field(..., min_length=0, max_length=500)  # empty allowed (treated as 0)
    elapsed_ms: int = Field(..., ge=0, le=3_600_000)


class SessionAnswerItem(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    question_id: str = Field(..., min_length=1, max_length=64)
    answer: str = Field(default="0", max_length=500)


class SessionComplete(BaseModel):
    """Batch complete a study session after client-side answering."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    total_elapsed_ms: int = Field(..., ge=0, le=86_400_000)
    answers: list[SessionAnswerItem] = Field(..., min_length=1, max_length=500)


class StartSession(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    subject_id: str = Field(..., min_length=1, max_length=MAX_SUBJECT_ID_LEN)
    level_id: str = Field(..., min_length=1, max_length=MAX_LEVEL_ID_LEN)

    @field_validator("subject_id")
    @classmethod
    def validate_subject_id(cls, v: str) -> str:
        if not SUBJECT_ID_PATTERN.match(v):
            raise ValueError("Invalid subject_id format")
        return v

    @field_validator("level_id")
    @classmethod
    def validate_level_id(cls, v: str) -> str:
        if not ID_PATTERN.match(v):
            raise ValueError("Invalid level_id format")
        return v


class StartAssessment(BaseModel):
    """Start a placement assessment for a subject (all levels)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    subject_id: str = Field(..., min_length=1, max_length=MAX_SUBJECT_ID_LEN)

    @field_validator("subject_id")
    @classmethod
    def validate_subject_id(cls, v: str) -> str:
        if not SUBJECT_ID_PATTERN.match(v):
            raise ValueError("Invalid subject_id format")
        return v


class PaymentSubmit(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    gcash_reference: str = Field(..., min_length=4, max_length=64)
    amount_php: float = Field(..., gt=0, le=100_000)
    notes: str = Field(default="", max_length=500)

    @field_validator("gcash_reference")
    @classmethod
    def validate_ref(cls, v: str) -> str:
        if not re.match(r"^[A-Za-z0-9_-]+$", v):
            raise ValueError("Invalid GCash reference format")
        return v


class PaymentVerify(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    status: str = Field(..., pattern=r"^(verified|rejected)$")
    notes: str = Field(default="", max_length=500)


def parse_body(model_cls: type[BaseModel], raw: str | bytes | None) -> BaseModel:
    """Parse JSON body into a Pydantic model. Raises ValueError on failure."""
    import json

    if raw is None or (isinstance(raw, str) and not raw.strip()):
        data: Any = {}
    else:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("Request body must be a JSON object")

    try:
        return model_cls.model_validate(data)
    except Exception as exc:
        # Pydantic ValidationError
        raise ValueError(str(exc)) from exc


def parse_csv_questions(csv_text: str) -> list[dict[str, str]]:
    """
    Parse CSV of questions.

    Formats supported (per row):
      - operand1,operator,operand2,equals,answer  e.g. 1,+,2,=,3
      - question,answer                         e.g. 1+2,3
    """
    import csv
    import io

    if not csv_text or not csv_text.strip():
        raise ValueError("CSV content is empty")

    # Strip BOM
    text = csv_text.lstrip("\ufeff")
    reader = csv.reader(io.StringIO(text))
    rows: list[dict[str, str]] = []

    for idx, row in enumerate(reader, start=1):
        if not row or all(not c.strip() for c in row):
            continue
        # Skip header-like rows
        first = row[0].strip().lower()
        if first in ("operand1", "question", "q", "left"):
            continue
        if len(rows) >= MAX_CSV_ROWS:
            raise ValueError(f"CSV exceeds maximum of {MAX_CSV_ROWS} rows")

        if len(row) >= 5:
            # 1,+,2,=,3
            a, op, b, eq, ans = [c.strip() for c in row[:5]]
            if eq != "=" and eq.lower() != "equals":
                # still accept if answer is last
                pass
            prompt = f"{a}{op}{b}"
            answer = ans
        elif len(row) >= 2:
            prompt = row[0].strip()
            answer = row[1].strip()
        else:
            raise ValueError(f"Row {idx}: expected at least 2 columns, got {len(row)}")

        if not prompt or not answer:
            raise ValueError(f"Row {idx}: empty question or answer")
        if len(prompt) > 500 or len(answer) > 200:
            raise ValueError(f"Row {idx}: question or answer too long")

        rows.append({"prompt": prompt, "answer": answer, "row": str(idx)})

    if not rows:
        raise ValueError("No valid question rows found in CSV")
    return rows
