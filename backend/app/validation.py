"""Input validation utilities."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict


# Shared constraints
MAX_TITLE_LEN = 200
MAX_DESC_LEN = 2000
MAX_SUMMARY_LEN = 4000
MAX_VIDEO_URL_LEN = 1000
# One carousel picture. Eight to twenty-four of these must still fit in one
# Lambda request (about 6 MB), so this is a thumbnail, not a full photo.
MAX_THUMB_BYTES = 400_000
MAX_THUMB_TEXT = 560_000
MAX_THUMB_BATCH_TEXT = 5_000_000
# Topic ids: one character plus up to 62 more is 63. Slugify uses this same
# limit so a long topic name is truncated instead of failing validation.
MAX_SUBJECT_ID_LEN = 63
MAX_LEVEL_ID_LEN = 64
MAX_CSV_ROWS = 5000
ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
SUBJECT_ID_PATTERN = re.compile(
    rf"^[a-z][a-z0-9_-]{{0,{MAX_SUBJECT_ID_LEN - 1}}}$"
)
# "Level 4" / "Set 4" / "lvl-4" → "4" (matches existing Vedic numeric ids).
_LEVEL_NUM_NAME = re.compile(r"^(?:level|set|lvl)[\s._-]+(\d+)$", re.IGNORECASE)


def ensure_subject_id(value: Any, *, allow_blank: bool = False) -> str | None:
    """Accept a topic id, or raise a message that names the field and the limit."""
    if value is None:
        if allow_blank:
            return None
        raise ValueError("Topic id is required")
    text = str(value).strip()
    if not text:
        if allow_blank:
            return None
        raise ValueError("Topic id is required")
    if len(text) > MAX_SUBJECT_ID_LEN:
        raise ValueError(
            f"Topic id must be at most {MAX_SUBJECT_ID_LEN} characters"
        )
    if not SUBJECT_ID_PATTERN.match(text):
        raise ValueError(
            "Topic id must be lowercase letters, numbers, hyphens, or underscores, "
            f"start with a letter, and be at most {MAX_SUBJECT_ID_LEN} characters"
        )
    return text


def normalize_level_id(value: str) -> str:
    """Accept human names like 'Level 4'; store a URL-safe id (no spaces)."""
    raw = (value or "").strip()
    if not raw:
        raise ValueError("level_id is required")
    numbered = _LEVEL_NUM_NAME.match(raw)
    if numbered:
        return numbered.group(1)
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw)
    slug = re.sub(r"-{2,}", "-", slug).strip("-_")
    if not slug or not ID_PATTERN.match(slug):
        raise ValueError(
            'level_id must be letters, numbers, hyphen, or underscore '
            '(no spaces). Use "4" or "level-4", not "Level 4".'
        )
    return slug[:MAX_LEVEL_ID_LEN]

# STEM subject categories (admin Content creation)
STEM_CATEGORIES = ("Science", "Technology", "Engineering", "Mathematics")

# Study content grade band (topic / subject attribute — not learner profile grade)
CONTENT_GRADE_LEVELS = (
    "Kindergarten",
    "Grade 1",
    "Grade 2",
    "Grade 3",
    "Grade 4",
    "Grade 5",
    "Grade 6",
    "Grade 7",
    "Grade 8",
    "Grade 9",
    "Grade 10",
    "Grade 11",
    "Grade 12",
)


def normalize_content_grade(value: str | None) -> str | None:
    """Return canonical grade_level label or None if empty/invalid."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    lower = raw.lower()
    if lower in ("k", "kg", "kindergarten", "kindergarden"):
        return "Kindergarten"
    # Accept "Grade 1", "grade1", "G1", "1"
    m = re.match(r"^(?:grade\s*|g\s*)?(\d{1,2})$", lower)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 12:
            return f"Grade {n}"
    for label in CONTENT_GRADE_LEVELS:
        if lower == label.lower():
            return label
    raise ValueError(
        f"grade_level must be Kindergarten or Grade 1–12 (got {value!r})"
    )


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


class FacebookFollowClaim(BaseModel):
    """POST /me/facebook/follow — honor-system claim after following on Facebook."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    display_name: str = Field(default="", max_length=80)
    handle: str = Field(default="", max_length=200)
    confirmed: bool = True


class FacebookEngagementClaim(BaseModel):
    """POST /me/facebook/engagement — comment / feedback / feature request."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    kind: str = Field(default="comment", max_length=32)
    display_name: str = Field(default="", max_length=80)
    text: str = Field(default="", max_length=2000)

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: str) -> str:
        k = (v or "comment").strip().lower()
        if k not in ("comment", "feedback", "feature_request"):
            raise ValueError("kind must be comment, feedback, or feature_request")
        return k


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


class SchoolRequest(BaseModel):
    """Public sign-up request to add a school not yet in the catalog."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(..., min_length=1, max_length=120)
    city: str = Field(default="", max_length=80)
    province: str = Field(default="", max_length=80)
    requester_email: str = Field(default="", max_length=160)

    @field_validator("requester_email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        email = (v or "").strip().lower()
        if not email:
            return ""
        if "@" not in email or "." not in email.split("@")[-1]:
            raise ValueError("Invalid requester_email")
        return email


class TaskCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str = Field(..., min_length=1, max_length=MAX_TITLE_LEN)
    description: str = Field(default="", max_length=MAX_DESC_LEN)
    due_date: str | None = Field(default=None, max_length=32)
    subject_id: str | None = Field(default=None, max_length=MAX_SUBJECT_ID_LEN)

    @field_validator("subject_id", mode="before")
    @classmethod
    def validate_subject_id(cls, v: str | None) -> str | None:
        return ensure_subject_id(v, allow_blank=True)


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class MasteryCreate(BaseModel):
    """POST /mastery — publish a mastery collection (personal or admin-shared)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(..., min_length=1, max_length=80)
    category: str = Field(..., min_length=1, max_length=32)
    topics: list[str] = Field(..., min_length=2, max_length=100)
    subject_ids: list[str] = Field(default_factory=list, max_length=200)
    start_date: str = Field(..., min_length=10, max_length=10)
    end_date: str = Field(..., min_length=10, max_length=10)
    # Admin-only: when true, collection appears for all learners
    shared: bool = False

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        for cat in STEM_CATEGORIES:
            if v.lower() == cat.lower():
                return cat
        raise ValueError(f"category must be one of: {', '.join(STEM_CATEGORIES)}")

    @field_validator("topics")
    @classmethod
    def validate_topics(cls, v: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in v or []:
            t = str(raw or "").strip()
            if not t:
                continue
            if len(t) > 100:
                raise ValueError("topic names must be at most 100 characters")
            key = t.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(t)
        if len(cleaned) < 2:
            raise ValueError("Select at least 2 topics")
        return cleaned

    @field_validator("subject_ids")
    @classmethod
    def validate_subject_ids(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in v or []:
            sid = ensure_subject_id(raw, allow_blank=True)
            if not sid:
                continue
            if sid in seen:
                continue
            seen.add(sid)
            out.append(sid)
        return out

    @field_validator("start_date", "end_date")
    @classmethod
    def validate_date(cls, v: str) -> str:
        s = (v or "").strip()
        if not _DATE_RE.match(s):
            raise ValueError("Dates must be YYYY-MM-DD")
        # Basic calendar sanity
        try:
            y, m, d = (int(x) for x in s.split("-"))
            if not (1 <= m <= 12 and 1 <= d <= 31):
                raise ValueError("invalid")
        except ValueError as exc:
            raise ValueError("Dates must be YYYY-MM-DD") from exc
        return s

    @model_validator(mode="after")
    def end_after_start(self) -> "MasteryCreate":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


# Full replace body for PUT /mastery/{id} (same shape as create)
MasteryUpdate = MasteryCreate


class TaskUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=MAX_TITLE_LEN)
    description: str | None = Field(default=None, max_length=MAX_DESC_LEN)
    due_date: str | None = Field(default=None, max_length=32)
    completed: bool | None = None
    subject_id: str | None = Field(default=None, max_length=MAX_SUBJECT_ID_LEN)

    @field_validator("subject_id", mode="before")
    @classmethod
    def validate_subject_id(cls, v: str | None) -> str | None:
        return ensure_subject_id(v, allow_blank=True)


def _validate_playlist_id(value: str | None) -> str | None:
    from app.playlists import clean_playlist_id

    return clean_playlist_id(value)


def _validate_playlist_name(value: str | None) -> str | None:
    from app.playlists import clean_playlist_name

    return clean_playlist_name(value)


def _reject_playlist_on_mathematics(model: Any) -> None:
    category = getattr(model, "category", None)
    if category != "Mathematics":
        return
    fields_set = getattr(model, "model_fields_set", set())
    for name in ("playlist_id", "playlist_name", "playlist_position", "playlist_sort"):
        if name not in fields_set:
            continue
        if getattr(model, name) is None:
            continue
        raise ValueError("Mathematics topics do not use playlists")


class PlaylistMembership(BaseModel):
    """Playlist fields shared by subject create, subject update, and science upload."""

    playlist_id: str | None = Field(default=None, max_length=32)
    playlist_name: str | None = Field(default=None, max_length=80)
    playlist_position: int | None = Field(default=None, ge=0, le=10000)
    playlist_sort: int | None = Field(default=None, ge=0, le=1000)

    @field_validator("playlist_id")
    @classmethod
    def validate_playlist_id(cls, v: str | None) -> str | None:
        return _validate_playlist_id(v)

    @field_validator("playlist_name")
    @classmethod
    def validate_playlist_name(cls, v: str | None) -> str | None:
        return _validate_playlist_name(v)

    @model_validator(mode="after")
    def reject_playlist_for_mathematics(self):
        _reject_playlist_on_mathematics(self)
        return self


class SubjectCreate(PlaylistMembership):
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
    # Study content grade band shown on Study landing (Kindergarten / Grade 1–12)
    grade_level: str | None = Field(default=None, max_length=32)

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

    @field_validator("subject_id", mode="before")
    @classmethod
    def validate_subject_id(cls, v: str | None) -> str | None:
        return ensure_subject_id(v, allow_blank=True)

    @field_validator("grade_level")
    @classmethod
    def validate_grade_level(cls, v: str | None) -> str | None:
        return normalize_content_grade(v)

    @model_validator(mode="after")
    def require_topic_or_name(self) -> "SubjectCreate":
        if not ((self.topic and self.topic.strip()) or (self.name and self.name.strip())):
            raise ValueError("topic is required (or provide name for legacy clients)")
        return self

    def resolved_topic(self) -> str:
        """Topic from explicit topic or legacy name field."""
        return (self.topic or self.name or "").strip()


class SubjectUpdate(PlaylistMembership):
    """Partial update of a subject (topic, description, category, sort_order, grade)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    category: str | None = Field(default=None, min_length=1, max_length=32)
    topic: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=MAX_DESC_LEN)
    sort_order: int | None = Field(default=None, ge=0, le=10000)
    grade_level: str | None = Field(default=None, max_length=32)

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        for cat in STEM_CATEGORIES:
            if v.lower() == cat.lower():
                return cat
        raise ValueError(f"category must be one of: {', '.join(STEM_CATEGORIES)}")

    @field_validator("grade_level")
    @classmethod
    def validate_grade_level(cls, v: str | None) -> str | None:
        # Allow explicit clear with empty string → stored as ""
        if v is None:
            return None
        if str(v).strip() == "":
            return ""
        return normalize_content_grade(v)


class TechnologyPageSpec(BaseModel):
    """One presentation page (image + optional audio/text) for folder import."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    index: int = Field(..., ge=0, le=200)
    title: str = Field(default="", max_length=160)
    text: str = Field(default="", max_length=8000)
    image_ext: str = Field(default="jpg", max_length=8)
    audio_ext: str = Field(default="", max_length=8)
    image_content_type: str = Field(default="image/jpeg", max_length=80)
    audio_content_type: str = Field(default="", max_length=80)


class ScienceTopicCreate(PlaylistMembership):
    """POST /science/topics — admin uploads a STEM lesson topic (any STEM category)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    category: str = Field(default="Science", min_length=1, max_length=32)
    topic: str = Field(..., min_length=1, max_length=100)
    grade_level: str | None = Field(default=None, max_length=32)
    # None (omitted or null) means "keep the saved value" when replacing a topic.
    summary: str | None = Field(default=None, max_length=MAX_SUMMARY_LEN)
    explainer_video_url: str | None = Field(default=None, max_length=MAX_VIDEO_URL_LEN)
    facebook_video_url: str | None = Field(default=None, max_length=MAX_VIDEO_URL_LEN)
    image_ext: str = Field(default="", max_length=8)
    image_content_type: str = Field(default="", max_length=80)
    html_ext: str = Field(default="", max_length=8)
    html_content_type: str = Field(default="", max_length=80)
    replace: bool = False

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        raw = (v or "Science").strip()
        for cat in STEM_CATEGORIES:
            if raw.lower() == cat.lower():
                return cat
        raise ValueError(f"category must be one of: {', '.join(STEM_CATEGORIES)}")

    @field_validator("grade_level")
    @classmethod
    def validate_grade_level(cls, v: str | None) -> str | None:
        # None (omitted or null) means keep. Explicit "" clears.
        if v is None:
            return None
        if str(v).strip() == "":
            return ""
        return normalize_content_grade(v)

    @field_validator("explainer_video_url")
    @classmethod
    def validate_video_url(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return _http_url(v, "Explainer video must be an http(s) URL")

    @field_validator("facebook_video_url")
    @classmethod
    def validate_facebook_video_url(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return _http_url(v, "Facebook video must be an http(s) URL")


class TechnologyTopicCreate(BaseModel):
    """POST /technology/topics — admin creates a Technology presentation topic."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    topic: str = Field(..., min_length=1, max_length=100)
    grade_level: str | None = Field(default=None, max_length=32)
    description: str = Field(default="", max_length=MAX_DESC_LEN)
    pages: list[TechnologyPageSpec] = Field(..., min_length=1, max_length=80)
    replace: bool = False

    @field_validator("grade_level")
    @classmethod
    def validate_grade_level(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if str(v).strip() == "":
            return ""
        return normalize_content_grade(v)


class LevelCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    level_id: str = Field(..., min_length=1, max_length=MAX_LEVEL_ID_LEN)
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=MAX_DESC_LEN)
    order: int = Field(..., ge=1, le=1000)
    pass_accuracy: float = Field(default=0.8, ge=0.0, le=1.0)
    min_questions: int = Field(default=5, ge=1, le=500)

    @field_validator("level_id", mode="before")
    @classmethod
    def validate_level_id(cls, v: str) -> str:
        return normalize_level_id("" if v is None else str(v))


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
    answer: str = Field(default="", max_length=500)


class SessionComplete(BaseModel):
    """Batch complete a study session after client-side answering."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    total_elapsed_ms: int = Field(..., ge=0, le=86_400_000)
    answers: list[SessionAnswerItem] = Field(..., min_length=1, max_length=500)


class RecommendationsUpdate(BaseModel):
    """PUT /recommendations — curated Home carousel.

    An empty list clears the curated set and Home falls back to
    top-visited (or random) topics. The Melon Manager bot uses this.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    subject_ids: list[str] = Field(default_factory=list, max_length=24)
    # Optional PNG or JPEG for a subject in subject_ids, as base64
    # (or a data:image/...;base64 URL). Omitted ids keep the picture
    # already stored for that lesson.
    thumbnails: dict[str, str] = Field(default_factory=dict)

    @field_validator("subject_ids")
    @classmethod
    def validate_subject_ids(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in values:
            sid = ensure_subject_id(raw, allow_blank=True)
            if not sid:
                continue
            if sid not in cleaned:
                cleaned.append(sid)
        return cleaned

    @field_validator("thumbnails")
    @classmethod
    def validate_thumbnails(cls, values: dict[str, str]) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for raw_id, raw_image in values.items():
            sid = ensure_subject_id(raw_id, allow_blank=True) or ""
            image = str(raw_image or "").strip()
            if not sid and not image:
                continue
            if not sid:
                raise ValueError(
                    "Topic id must be lowercase letters, numbers, hyphens, or underscores, "
                    f"start with a letter, and be at most {MAX_SUBJECT_ID_LEN} characters"
                )
            if not image:
                continue
            decode_thumbnail_image(image)
            cleaned[sid] = image
        if sum(len(image) for image in cleaned.values()) > MAX_THUMB_BATCH_TEXT:
            raise ValueError(
                "Thumbnails together are too large for one request. "
                "Send smaller JPEG files, or fewer images at a time."
            )
        return cleaned

    @model_validator(mode="after")
    def thumbnails_belong_to_the_list(self) -> "RecommendationsUpdate":
        for sid in self.thumbnails:
            if sid not in self.subject_ids:
                raise ValueError(f"Thumbnail is not in subject_ids: '{sid}'")
        return self


class TopicAccessPing(BaseModel):
    """POST /study/access — learner heartbeat while a topic is open."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    subject_id: str = Field(..., min_length=1, max_length=MAX_SUBJECT_ID_LEN)
    elapsed_ms: int = Field(default=0, ge=0, le=300_000)

    @field_validator("subject_id", mode="before")
    @classmethod
    def validate_subject_id(cls, v: str) -> str:
        subject_id = ensure_subject_id(v)
        if not subject_id:
            raise ValueError("Topic id is required")
        return subject_id


class StartSession(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    subject_id: str = Field(..., min_length=1, max_length=MAX_SUBJECT_ID_LEN)
    level_id: str = Field(..., min_length=1, max_length=MAX_LEVEL_ID_LEN)

    @field_validator("subject_id", mode="before")
    @classmethod
    def validate_subject_id(cls, v: str) -> str:
        subject_id = ensure_subject_id(v)
        if not subject_id:
            raise ValueError("Topic id is required")
        return subject_id

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

    @field_validator("subject_id", mode="before")
    @classmethod
    def validate_subject_id(cls, v: str) -> str:
        subject_id = ensure_subject_id(v)
        if not subject_id:
            raise ValueError("Topic id is required")
        return subject_id


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


def _http_url(value: str, message: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if not (raw.startswith("http://") or raw.startswith("https://")):
        raise ValueError(message)
    return raw


def decode_thumbnail_image(raw: str) -> tuple[bytes, str, str]:
    """Return PNG or JPEG bytes, file extension, and content type.

    Accepts plain base64 or a data:image/...;base64 URL. Rejects http(s) links.
    """
    import base64
    import binascii

    text = "".join(str(raw or "").split())
    lowered = text.lower()
    if lowered.startswith("http://") or lowered.startswith("https://"):
        raise ValueError("Thumbnail must be a PNG or JPEG image, not a URL")
    if lowered.startswith("data:"):
        comma = text.find(",")
        if comma < 0 or ";base64" not in lowered[:comma]:
            raise ValueError("Thumbnail image is not valid base64")
        text = text[comma + 1 :]
    if len(text) > MAX_THUMB_TEXT:
        raise ValueError("Thumbnail image must be 400 KB or smaller")
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Thumbnail image is not valid base64") from exc
    if not data:
        raise ValueError("Thumbnail image is empty")
    if len(data) > MAX_THUMB_BYTES:
        raise ValueError("Thumbnail image must be 400 KB or smaller")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return data, "png", "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return data, "jpg", "image/jpeg"
    raise ValueError("Thumbnail must be a PNG or JPEG image")


def _subject_id_or_name_length_message(exc: Exception) -> str | None:
    """Friendlier text for topic-id and name-length errors only.

    Every other validation error keeps Pydantic's own message.
    """
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return None
    parts: list[str] = []
    for err in errors():
        msg = str(err.get("msg") or "Invalid value")
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, ") :]
        loc = [str(part) for part in (err.get("loc") or ()) if str(part) != "body"]
        field = loc[-1] if loc else ""
        kind = str(err.get("type") or "")
        is_subject_id = field == "subject_id" or msg.startswith("Topic id")
        is_name_length = field in ("topic", "name") and (
            kind in ("string_too_long", "string_too_short")
            or "at most" in msg
            or "at least" in msg
        )
        if not is_subject_id and not is_name_length:
            return None
        if field and field.lower() not in msg.lower() and not msg.startswith("Topic id"):
            parts.append(f"{field}: {msg}")
        else:
            parts.append(msg)
    return "; ".join(parts) if parts else None


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
        friendly = _subject_id_or_name_length_message(exc)
        if friendly:
            raise ValueError(friendly) from exc
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
