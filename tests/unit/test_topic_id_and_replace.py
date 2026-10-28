"""Long topic ids, and replace uploads that must not clobber saved fields."""

from __future__ import annotations

import json
import re

import boto3
import pytest

from app import db, keys
from app.handler import handler
from app.services import subject_service
from app.services.subject_service import (
    LevelNotFound,
    find_live_topic,
    get_level,
    normalize_topic_name,
    seed_math_defaults,
    slugify_subject_id,
)
from app.validation import MAX_SUBJECT_ID_LEN, SUBJECT_ID_PATTERN, SubjectCreate
from tests.helpers import make_event

REGION = "ap-southeast-1"
BUCKET = "stem-test-frontend"


def _s3_env(monkeypatch):
    monkeypatch.setenv("FRONTEND_BUCKET", BUCKET)
    monkeypatch.setenv("FRONTEND_PUBLIC_BASE", "http://example.com")
    s3 = boto3.client("s3", region_name=REGION)
    try:
        s3.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )
    except Exception:
        pass


def _post(path, headers, body):
    return handler(
        make_event("POST", path, headers=headers, body=json.dumps(body))
    )


def _error(resp) -> str:
    return json.loads(resp["body"]).get("error") or ""


@pytest.mark.unit
def test_slugify_respects_shared_limit_and_does_not_end_with_hyphen():
    assert MAX_SUBJECT_ID_LEN == 63
    assert SUBJECT_ID_PATTERN.match("a" * MAX_SUBJECT_ID_LEN)
    assert not SUBJECT_ID_PATTERN.match("a" * (MAX_SUBJECT_ID_LEN + 1))

    long_name = (
        "Photosynthesis and cellular respiration in tropical rainforest "
        "ecosystems extra words about energy transfer"
    )
    assert len(long_name) >= 95
    slug = slugify_subject_id("Science", long_name)
    assert len(slug) <= MAX_SUBJECT_ID_LEN
    assert SUBJECT_ID_PATTERN.match(slug)
    assert not slug.endswith("-") and not slug.endswith("_")

    # The cut lands on a hyphen: mathematics- + ("ab-" * n)[50] == "-".
    hyphenated = slugify_subject_id("Mathematics", "ab-" * 40)
    assert len(hyphenated) <= MAX_SUBJECT_ID_LEN
    assert not hyphenated.endswith("-")
    assert SUBJECT_ID_PATTERN.match(hyphenated)

    prefix = "science-"
    exact = slugify_subject_id("Science", "a" * (MAX_SUBJECT_ID_LEN - len(prefix)))
    assert len(exact) == MAX_SUBJECT_ID_LEN
    assert SUBJECT_ID_PATTERN.match(exact)

    over = slugify_subject_id("Science", "a" * (MAX_SUBJECT_ID_LEN - len(prefix) + 5))
    assert len(over) == MAX_SUBJECT_ID_LEN
    assert SUBJECT_ID_PATTERN.match(over)
    assert not over.endswith("-")

    empty = slugify_subject_id("***", "???")
    assert re.fullmatch(r"subject-[0-9a-f]{7}", empty)
    assert SUBJECT_ID_PATTERN.match(empty)


@pytest.mark.unit
def test_long_topic_name_saves_on_science_and_math(dynamodb_table, admin_headers, monkeypatch):
    _s3_env(monkeypatch)
    topic = (
        "Photosynthesis and cellular respiration in tropical rainforest "
        "ecosystems extra words about energy"
    )
    assert 90 <= len(topic) <= 100

    science = _post(
        "/science/topics",
        admin_headers,
        {
            "category": "Science",
            "topic": topic,
            "grade_level": "Grade 4",
            "summary": "Leaves capture sunlight.",
        },
    )
    assert science["statusCode"] == 201, science["body"]
    science_id = json.loads(science["body"])["subject_id"]
    assert len(science_id) <= MAX_SUBJECT_ID_LEN
    assert SUBJECT_ID_PATTERN.match(science_id)
    assert not science_id.endswith("-")

    math = _post(
        "/subjects",
        admin_headers,
        {"category": "Mathematics", "topic": topic, "description": "Long math topic"},
    )
    assert math["statusCode"] == 201, math["body"]
    math_id = json.loads(math["body"])["subject_id"]
    assert len(math_id) <= MAX_SUBJECT_ID_LEN
    assert SUBJECT_ID_PATTERN.match(math_id)
    assert not math_id.endswith("-")
    assert math_id != science_id


@pytest.mark.unit
def test_subject_id_exactly_at_limit_saves_and_over_limit_is_clear(
    dynamodb_table, admin_headers
):
    exact_id = "m" + "a" * (MAX_SUBJECT_ID_LEN - 1)
    assert len(exact_id) == MAX_SUBJECT_ID_LEN
    ok = _post(
        "/subjects",
        admin_headers,
        {
            "category": "Mathematics",
            "topic": "Exact limit",
            "subject_id": exact_id,
        },
    )
    assert ok["statusCode"] == 201, ok["body"]
    assert json.loads(ok["body"])["subject_id"] == exact_id

    too_long = exact_id + "z"
    assert len(too_long) == MAX_SUBJECT_ID_LEN + 1
    bad = _post(
        "/subjects",
        admin_headers,
        {
            "category": "Mathematics",
            "topic": "Too long",
            "subject_id": too_long,
        },
    )
    assert bad["statusCode"] == 422
    assert _error(bad) == f"Topic id must be at most {MAX_SUBJECT_ID_LEN} characters"

    invalid = _post(
        "/subjects",
        admin_headers,
        {
            "category": "Mathematics",
            "topic": "Bad id",
            "subject_id": "Math!",
        },
    )
    assert invalid["statusCode"] == 422
    message = _error(invalid)
    assert "Topic id" in message
    assert str(MAX_SUBJECT_ID_LEN) in message


@pytest.mark.unit
def test_other_validation_errors_keep_the_pydantic_body(dynamodb_table, user_headers):
    resp = handler(
        make_event("POST", "/tasks", headers=user_headers, body=json.dumps({}))
    )
    assert resp["statusCode"] == 422
    assert "validation error" in _error(resp)


@pytest.mark.unit
def test_replace_with_defaults_keeps_grade_and_summary(
    dynamodb_table, admin_headers, monkeypatch
):
    _s3_env(monkeypatch)
    created = _post(
        "/science/topics",
        admin_headers,
        {
            "category": "Science",
            "topic": "Volcanoes",
            "grade_level": "Grade 6",
            "summary": "Magma rises and erupts.",
            "explainer_video_url": "https://www.youtube.com/watch?v=keepme",
        },
    )
    assert created["statusCode"] == 201, created["body"]

    # Omitted summary and null grade are the form defaults once the admin
    # has not edited those fields. main overwrote both.
    replaced = _post(
        "/science/topics",
        admin_headers,
        {
            "category": "Science",
            "topic": "Volcanoes",
            "grade_level": None,
            "replace": True,
        },
    )
    assert replaced["statusCode"] == 201, replaced["body"]
    body = json.loads(replaced["body"])
    assert body["grade_level"] == "Grade 6"
    assert body["summary"] == "Magma rises and erupts."
    assert body["explainer_video_url"] == "https://www.youtube.com/watch?v=keepme"


@pytest.mark.unit
def test_replace_with_explicit_values_updates_grade_and_summary(
    dynamodb_table, admin_headers, monkeypatch
):
    _s3_env(monkeypatch)
    created = _post(
        "/science/topics",
        admin_headers,
        {
            "category": "Science",
            "topic": "Volcanoes",
            "grade_level": "Grade 6",
            "summary": "Magma rises and erupts.",
            "explainer_video_url": "https://www.youtube.com/watch?v=keepme",
        },
    )
    assert created["statusCode"] == 201, created["body"]

    replaced = _post(
        "/science/topics",
        admin_headers,
        {
            "category": "Science",
            "topic": "Volcanoes",
            "grade_level": "Grade 8",
            "summary": "A revised summary about magma and ash.",
            "explainer_video_url": "https://www.youtube.com/watch?v=newvideo",
            "replace": True,
        },
    )
    assert replaced["statusCode"] == 201, replaced["body"]
    body = json.loads(replaced["body"])
    assert body["grade_level"] == "Grade 8"
    assert body["summary"] == "A revised summary about magma and ash."
    assert body["explainer_video_url"] == "https://www.youtube.com/watch?v=newvideo"

    # Grade 3 is the upload form's untouched default. Sent on purpose, it still saves.
    chosen_default = _post(
        "/science/topics",
        admin_headers,
        {
            "category": "Science",
            "topic": "Volcanoes",
            "grade_level": "Grade 3",
            "summary": "A revised summary about magma and ash.",
            "replace": True,
        },
    )
    assert chosen_default["statusCode"] == 201, chosen_default["body"]
    chosen = json.loads(chosen_default["body"])
    assert chosen["grade_level"] == "Grade 3"
    assert chosen["summary"] == "A revised summary about magma and ash."


@pytest.mark.unit
def test_technology_replace_keeps_grade_unless_explicit(
    dynamodb_table, admin_headers, monkeypatch
):
    _s3_env(monkeypatch)
    pages = [
        {
            "index": 0,
            "title": "Intro",
            "text": "Hello robots",
            "image_ext": "jpg",
            "image_content_type": "image/jpeg",
        }
    ]
    created = _post(
        "/technology/topics",
        admin_headers,
        {
            "topic": "Robots",
            "grade_level": "Grade 6",
            "description": "How robots move.",
            "pages": pages,
        },
    )
    assert created["statusCode"] == 201, created["body"]
    assert json.loads(created["body"])["grade_level"] == "Grade 6"

    kept = _post(
        "/technology/topics",
        admin_headers,
        {"topic": "Robots", "replace": True, "pages": pages},
    )
    assert kept["statusCode"] == 201, kept["body"]
    assert json.loads(kept["body"])["grade_level"] == "Grade 6"

    changed = _post(
        "/technology/topics",
        admin_headers,
        {
            "topic": "Robots",
            "grade_level": "Grade 1",
            "replace": True,
            "pages": pages,
        },
    )
    assert changed["statusCode"] == 201, changed["body"]
    assert json.loads(changed["body"])["grade_level"] == "Grade 1"


@pytest.mark.unit
def test_truncated_names_and_fallbacks_do_not_collide(dynamodb_table, admin_headers, monkeypatch):
    _s3_env(monkeypatch)
    one_name = "a" * 60 + "ONE"
    two_name = "a" * 60 + "TWO"
    one_id = slugify_subject_id("Science", one_name)
    two_id = slugify_subject_id("Science", two_name)
    assert one_id != two_id
    assert len(one_id) <= MAX_SUBJECT_ID_LEN
    assert len(two_id) <= MAX_SUBJECT_ID_LEN
    assert SUBJECT_ID_PATTERN.match(one_id)
    assert SUBJECT_ID_PATTERN.match(two_id)
    assert one_id == slugify_subject_id("Science", one_name)
    assert re.search(r"-[0-9a-f]{7}$", one_id)

    symbols = slugify_subject_id("Science", "???")
    other_symbols = slugify_subject_id("Science", "@@@")
    non_latin = slugify_subject_id("Science", "光合作用")
    assert re.fullmatch(r"science-[0-9a-f]{7}", symbols)
    assert symbols != other_symbols
    assert symbols != non_latin
    assert non_latin.startswith("science-")
    assert symbols == slugify_subject_id("Science", "???")

    one = _post(
        "/science/topics",
        admin_headers,
        {"category": "Science", "topic": one_name, "grade_level": "Grade 5", "summary": "First"},
    )
    two = _post(
        "/science/topics",
        admin_headers,
        {"category": "Science", "topic": two_name, "grade_level": "Grade 2", "summary": "Second"},
    )
    assert one["statusCode"] == 201, one["body"]
    assert two["statusCode"] == 201, two["body"]
    assert json.loads(one["body"])["subject_id"] == one_id
    assert json.loads(two["body"])["subject_id"] == two_id

    stolen = slugify_subject_id("Science", "???")
    holder = _post(
        "/subjects",
        admin_headers,
        {
            "category": "Science",
            "topic": "Kept Name",
            "subject_id": stolen,
            "description": "do not lose this",
            "grade_level": "Grade 9",
        },
    )
    assert holder["statusCode"] == 201, holder["body"]
    db.update_item(
        keys.subject_pk(stolen),
        keys.subject_meta_sk(),
        {"content_kind": "lesson", "science_ready": True},
    )
    refused = _post(
        "/science/topics",
        admin_headers,
        {
            "category": "Science",
            "topic": "???",
            "grade_level": "Grade 1",
            "summary": "should not land",
            "replace": True,
        },
    )
    assert refused["statusCode"] == 409, refused["body"]
    message = _error(refused)
    assert "Kept Name" in message
    assert "???" in message
    assert "Rename this topic, or use Edit Subject to change the existing one." in message
    listed = handler(make_event("GET", "/subjects", headers=admin_headers))
    rows = json.loads(listed["body"])["subjects"]
    kept = next(row for row in rows if row["subject_id"] == stolen)
    assert kept["topic"] == "Kept Name"
    assert kept["grade_level"] == "Grade 9"


@pytest.mark.unit
def test_accents_fold_into_the_slug():
    slug = slugify_subject_id("Science", "Éclairs ñandú")
    assert slug == "science-eclairs-nandu"
    assert SUBJECT_ID_PATTERN.match(slug)


@pytest.mark.unit
def test_explicit_empty_summary_clears_description(
    dynamodb_table, admin_headers, monkeypatch
):
    _s3_env(monkeypatch)
    created = _post(
        "/science/topics",
        admin_headers,
        {
            "category": "Science",
            "topic": "Moss",
            "grade_level": "Grade 4",
            "summary": "Moss grows in damp shade.",
        },
    )
    assert created["statusCode"] == 201, created["body"]
    assert json.loads(created["body"])["description"] == "Moss grows in damp shade."

    cleared = _post(
        "/science/topics",
        admin_headers,
        {
            "category": "Science",
            "topic": "Moss",
            "summary": "",
            "replace": True,
        },
    )
    assert cleared["statusCode"] == 201, cleared["body"]
    body = json.loads(cleared["body"])
    assert body["summary"] == ""
    assert body["description"] == ""
    assert body["grade_level"] == "Grade 4"


@pytest.mark.unit
def test_explicit_empty_grade_clears_and_null_keeps(
    dynamodb_table, admin_headers, monkeypatch
):
    _s3_env(monkeypatch)
    created = _post(
        "/science/topics",
        admin_headers,
        {
            "category": "Science",
            "topic": "Lakes",
            "grade_level": "Grade 6",
            "summary": "Still water.",
        },
    )
    assert created["statusCode"] == 201, created["body"]

    kept = _post(
        "/science/topics",
        admin_headers,
        {"category": "Science", "topic": "Lakes", "grade_level": None, "replace": True},
    )
    assert kept["statusCode"] == 201, kept["body"]
    assert json.loads(kept["body"])["grade_level"] == "Grade 6"

    cleared = _post(
        "/science/topics",
        admin_headers,
        {"category": "Science", "topic": "Lakes", "grade_level": "", "replace": True},
    )
    assert cleared["statusCode"] == 201, cleared["body"]
    assert not json.loads(cleared["body"])["grade_level"]
    assert json.loads(cleared["body"])["summary"] == "Still water."


def _plant_legacy(subject_id: str, topic: str, *, grade: str, summary: str):
    subject_service.create_subject(
        SubjectCreate(
            subject_id=subject_id,
            category="Science",
            topic=topic,
            description=summary,
            grade_level=grade,
            sort_order=1,
        )
    )
    db.update_item(
        keys.subject_pk(subject_id),
        keys.subject_meta_sk(),
        {"summary": summary, "content_kind": "lesson", "science_ready": True},
    )


def _subjects(headers):
    listed = handler(make_event("GET", "/subjects", headers=headers))
    assert listed["statusCode"] == 200, listed["body"]
    return json.loads(listed["body"])["subjects"]


@pytest.mark.unit
def test_legacy_ids_are_replaced_in_place(dynamodb_table, admin_headers, monkeypatch):
    _s3_env(monkeypatch)
    accent = "Éclairs ñandú"
    assert slugify_subject_id("Science", accent) == "science-eclairs-nandu"
    _plant_legacy("science-clairs-and", accent, grade="Grade 7", summary="legacy")
    _plant_legacy("science", "???", grade="Grade 7", summary="legacy")

    accent_replace = _post(
        "/science/topics",
        admin_headers,
        {"category": "Science", "topic": accent, "replace": True},
    )
    assert accent_replace["statusCode"] == 201, accent_replace["body"]
    accent_body = json.loads(accent_replace["body"])
    assert accent_body["subject_id"] == "science-clairs-and"
    assert accent_body["grade_level"] == "Grade 7"
    assert accent_body["summary"] == "legacy"
    assert accent_body["topic"] == accent

    symbol_replace = _post(
        "/science/topics",
        admin_headers,
        {"category": "Science", "topic": "???", "replace": True},
    )
    assert symbol_replace["statusCode"] == 201, symbol_replace["body"]
    symbol_body = json.loads(symbol_replace["body"])
    assert symbol_body["subject_id"] == "science"
    assert symbol_body["grade_level"] == "Grade 7"
    assert symbol_body["summary"] == "legacy"
    assert symbol_body["topic"] == "???"

    rows = _subjects(admin_headers)
    accent_rows = [row for row in rows if normalize_topic_name(row["topic"]) == normalize_topic_name(accent)]
    symbol_rows = [row for row in rows if row["topic"] == "???"]
    assert [row["subject_id"] for row in accent_rows] == ["science-clairs-and"]
    assert [row["subject_id"] for row in symbol_rows] == ["science"]
    assert all(row["subject_id"] != "science-eclairs-nandu" for row in rows)


@pytest.mark.unit
def test_whitespace_and_case_do_not_rename_or_conflict(
    dynamodb_table, admin_headers, monkeypatch
):
    _s3_env(monkeypatch)
    assert normalize_topic_name("a b") == normalize_topic_name("a  b")
    long_one = ("word " * 30) + "ONE"
    long_two = long_one.replace("word ", "word  ", 1)
    assert slugify_subject_id("Science", long_one) == slugify_subject_id("Science", long_two)

    created = _post(
        "/science/topics",
        admin_headers,
        {
            "category": "Science",
            "topic": "Photosynthesis",
            "grade_level": "Grade 6",
            "summary": "keep",
        },
    )
    assert created["statusCode"] == 201, created["body"]
    original_id = json.loads(created["body"])["subject_id"]

    replaced = _post(
        "/science/topics",
        admin_headers,
        {"category": "Science", "topic": "photosynthesis", "replace": True},
    )
    assert replaced["statusCode"] == 201, replaced["body"]
    body = json.loads(replaced["body"])
    assert body["subject_id"] == original_id
    assert body["topic"] == "Photosynthesis"
    assert body["grade_level"] == "Grade 6"
    assert body["summary"] == "keep"

    spaced = _post(
        "/science/topics",
        admin_headers,
        {
            "category": "Science",
            "topic": "a b",
            "grade_level": "Grade 8",
            "summary": "spaced",
        },
    )
    assert spaced["statusCode"] == 201, spaced["body"]
    spaced_id = json.loads(spaced["body"])["subject_id"]
    again = _post(
        "/science/topics",
        admin_headers,
        {"category": "Science", "topic": "a  b", "replace": True},
    )
    assert again["statusCode"] == 201, again["body"]
    again_body = json.loads(again["body"])
    assert again_body["subject_id"] == spaced_id
    assert again_body["topic"] == "a b"
    assert again_body["grade_level"] == "Grade 8"
    assert again_body["summary"] == "spaced"
    assert again["statusCode"] != 409


def _put_subject(
    subject_id: str,
    topic: str,
    *,
    category: str = "Science",
    deleted_at: str = "",
    content_kind: str = "",
    science_ready: bool = False,
    grade_level: str = "Grade 1",
):
    db.put_item(
        {
            "PK": keys.subject_pk(subject_id),
            "SK": keys.subject_meta_sk(),
            "GSI1PK": keys.ENTITY_SUBJECT,
            "GSI1SK": f"00000#{subject_id}",
            "entity_type": "SUBJECT",
            "subject_id": subject_id,
            "category": category,
            "topic": topic,
            "name": topic,
            "description": "",
            "summary": "planted",
            "grade_level": grade_level,
            "content_kind": content_kind,
            "science_ready": science_ready,
            "sort_order": 0,
            "deleted_at": deleted_at,
        }
    )


@pytest.mark.unit
def test_find_live_topic_lowest_id_and_skips_deleted(dynamodb_table):
    _put_subject("m-shared", "Shared", deleted_at="2020-01-01T00:00:00Z")
    _put_subject("b-shared", "Shared")
    _put_subject("a-shared", "Shared")
    found = find_live_topic("Science", "Shared")
    assert found is not None
    assert found["subject_id"] == "a-shared"
    _put_subject("only-gone", "Gone", deleted_at="2020-01-01T00:00:00Z")
    assert find_live_topic("Science", "Gone") is None


@pytest.mark.unit
def test_upload_does_not_convert_a_quiz(dynamodb_table, admin_headers, monkeypatch):
    _s3_env(monkeypatch)
    subject_service.create_subject(
        SubjectCreate(
            subject_id="science-quizzy",
            category="Science",
            topic="Quizzy",
            description="A question bank",
            grade_level="Grade 4",
        )
    )
    blocked = _post(
        "/science/topics",
        admin_headers,
        {"category": "Science", "topic": "Quizzy"},
    )
    assert blocked["statusCode"] == 400, blocked["body"]
    assert "Rename this topic." in _error(blocked)
    assert "quiz" in _error(blocked).lower()

    replaced = _post(
        "/science/topics",
        admin_headers,
        {
            "category": "Science",
            "topic": "Quizzy",
            "replace": True,
            "grade_level": "Grade 1",
            "summary": "should not land",
        },
    )
    assert replaced["statusCode"] == 400, replaced["body"]
    assert "Rename this topic." in _error(replaced)
    quiz = next(row for row in _subjects(admin_headers) if row["subject_id"] == "science-quizzy")
    assert quiz["content_kind"] != "lesson"
    assert quiz["grade_level"] == "Grade 4"
    assert quiz["topic"] == "Quizzy"


@pytest.mark.unit
def test_math_create_reuses_normalized_name_as_conflict(dynamodb_table, admin_headers):
    subject_service.create_subject(
        SubjectCreate(
            subject_id="mathematics-legacy-x",
            category="Mathematics",
            topic="Añadir",
            description="legacy",
        )
    )
    assert slugify_subject_id("Mathematics", "Añadir") == "mathematics-anadir"
    duplicate = _post(
        "/subjects",
        admin_headers,
        {"category": "Mathematics", "topic": "Añadir"},
    )
    assert duplicate["statusCode"] == 400, duplicate["body"]
    assert "mathematics-legacy-x" in _error(duplicate)
    assert "already exists" in _error(duplicate)
    rows = _subjects(admin_headers)
    matched = [row for row in rows if normalize_topic_name(row["topic"]) == normalize_topic_name("Añadir")]
    assert [row["subject_id"] for row in matched] == ["mathematics-legacy-x"]


@pytest.mark.unit
def test_german_sharp_s_matches_across_case(dynamodb_table, admin_headers, monkeypatch):
    _s3_env(monkeypatch)
    assert normalize_topic_name("Straße") == normalize_topic_name("STRASSE")
    assert normalize_topic_name("Straße") == "strasse"
    assert normalize_topic_name("ς") == normalize_topic_name("Σ")
    created = _post(
        "/science/topics",
        admin_headers,
        {
            "category": "Science",
            "topic": "Straße",
            "grade_level": "Grade 6",
            "summary": "keep",
        },
    )
    assert created["statusCode"] == 201, created["body"]
    original_id = json.loads(created["body"])["subject_id"]
    replaced = _post(
        "/science/topics",
        admin_headers,
        {"category": "Science", "topic": "STRASSE", "replace": True},
    )
    assert replaced["statusCode"] == 201, replaced["body"]
    body = json.loads(replaced["body"])
    assert body["subject_id"] == original_id
    assert body["topic"] == "Straße"
    assert body["grade_level"] == "Grade 6"
    assert body["summary"] == "keep"
    assert normalize_topic_name("\ufeffStraße") == "strasse"
    assert normalize_topic_name("a\u0085b") == "a b"
    assert normalize_topic_name("a\u001cb") == "a b"
    assert normalize_topic_name("a\u001fb") == "a b"


@pytest.mark.unit
def test_quiz_id_clash_names_the_id(dynamodb_table, admin_headers, monkeypatch):
    _s3_env(monkeypatch)
    subject_service.create_subject(
        SubjectCreate(
            subject_id="science-plants",
            category="Science",
            topic="Not Plants",
            description="quiz bank",
            grade_level="Grade 2",
        )
    )
    blocked = _post(
        "/science/topics",
        admin_headers,
        {"category": "Science", "topic": "Plants", "replace": True},
    )
    assert blocked["statusCode"] == 400, blocked["body"]
    message = _error(blocked)
    assert "topic id" in message
    assert "science-plants" in message
    assert "Rename this topic." in message
    quiz = next(row for row in _subjects(admin_headers) if row["subject_id"] == "science-plants")
    assert quiz["topic"] == "Not Plants"
    assert quiz["content_kind"] != "lesson"


@pytest.mark.unit
def test_update_subject_rejects_duplicate_normalized_name(dynamodb_table, admin_headers):
    subject_service.create_subject(
        SubjectCreate(
            subject_id="mathematics-legacy-x",
            category="Mathematics",
            topic="Añadir",
        )
    )
    subject_service.create_subject(
        SubjectCreate(
            subject_id="mathematics-fractions",
            category="Mathematics",
            topic="Fractions",
        )
    )
    taken = handler(
        make_event(
            "PUT",
            "/subjects/mathematics-fractions",
            headers=admin_headers,
            body=json.dumps({"topic": "Añadir"}),
        )
    )
    assert taken["statusCode"] == 409, taken["body"]
    assert "mathematics-legacy-x" in _error(taken)
    assert "already exists" in _error(taken)
    fractions = next(
        row for row in _subjects(admin_headers) if row["subject_id"] == "mathematics-fractions"
    )
    assert fractions["topic"] == "Fractions"

    same = handler(
        make_event(
            "PUT",
            "/subjects/mathematics-fractions",
            headers=admin_headers,
            body=json.dumps({"topic": "Fractions"}),
        )
    )
    assert same["statusCode"] == 200, same["body"]
    assert json.loads(same["body"])["topic"] == "Fractions"

    cased = handler(
        make_event(
            "PUT",
            "/subjects/mathematics-legacy-x",
            headers=admin_headers,
            body=json.dumps({"topic": "añadir"}),
        )
    )
    assert cased["statusCode"] == 200, cased["body"]
    assert json.loads(cased["body"])["topic"] == "añadir"


@pytest.mark.unit
def test_seed_math_defaults_uses_the_existing_arithmetic_subject(dynamodb_table):
    subject_service.create_subject(
        SubjectCreate(
            subject_id="mathematics-legacy-x",
            category="Mathematics",
            topic="Arithmetic",
            description="legacy",
        )
    )
    result = seed_math_defaults()
    assert result["subject_created"] is False
    assert result["levels_created"] == 3
    assert result["questions_imported"] > 0
    assert len(subject_service.list_levels("mathematics-legacy-x")) == 3
    with pytest.raises(LevelNotFound):
        get_level("math", "l1")
