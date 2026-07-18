"""Integration tests – task CRUD via handler."""

import json

import pytest

from app.handler import handler
from tests.helpers import make_event


@pytest.mark.integration
def test_task_crud_owner_only(dynamodb_table, user_headers):
    # Create
    event = make_event(
        "POST",
        "/tasks",
        headers=user_headers,
        body=json.dumps({"title": "Practice addition", "description": "10 mins"}),
    )
    resp = handler(event)
    assert resp["statusCode"] == 201
    task = json.loads(resp["body"])
    assert task["title"] == "Practice addition"
    task_id = task["task_id"]

    # List
    resp = handler(make_event("GET", "/tasks", headers=user_headers))
    assert resp["statusCode"] == 200
    tasks = json.loads(resp["body"])["tasks"]
    assert len(tasks) == 1

    # Get
    resp = handler(make_event("GET", f"/tasks/{task_id}", headers=user_headers))
    assert resp["statusCode"] == 200

    # Update
    resp = handler(
        make_event(
            "PUT",
            f"/tasks/{task_id}",
            headers=user_headers,
            body=json.dumps({"completed": True}),
        )
    )
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["completed"] is True

    # Soft delete
    resp = handler(make_event("DELETE", f"/tasks/{task_id}", headers=user_headers))
    assert resp["statusCode"] == 204

    # Gone
    resp = handler(make_event("GET", f"/tasks/{task_id}", headers=user_headers))
    assert resp["statusCode"] == 404

    resp = handler(make_event("GET", "/tasks", headers=user_headers))
    assert json.loads(resp["body"])["tasks"] == []


@pytest.mark.integration
def test_task_validation_error(dynamodb_table, user_headers):
    resp = handler(
        make_event("POST", "/tasks", headers=user_headers, body=json.dumps({}))
    )
    assert resp["statusCode"] == 422


@pytest.mark.integration
def test_other_user_cannot_see_task(dynamodb_table, user_headers):
    resp = handler(
        make_event(
            "POST",
            "/tasks",
            headers=user_headers,
            body=json.dumps({"title": "Secret"}),
        )
    )
    task_id = json.loads(resp["body"])["task_id"]

    other = {"x-test-user": "other-user", "x-test-email": "o@e.com"}
    resp = handler(make_event("GET", f"/tasks/{task_id}", headers=other))
    assert resp["statusCode"] == 404

    resp = handler(make_event("GET", "/tasks", headers=other))
    assert json.loads(resp["body"])["tasks"] == []
