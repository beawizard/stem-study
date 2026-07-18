"""Unit tests for HTTP response helpers."""

import json

from app.response import (
    bad_request,
    created,
    no_content,
    ok,
    payment_required,
    unauthorized,
)


def test_ok_default():
    r = ok()
    assert r["statusCode"] == 200
    assert json.loads(r["body"])["ok"] is True


def test_ok_body():
    r = ok({"a": 1})
    assert json.loads(r["body"]) == {"a": 1}


def test_created():
    r = created({"id": "1"})
    assert r["statusCode"] == 201


def test_no_content():
    r = no_content()
    assert r["statusCode"] == 204


def test_errors():
    assert unauthorized()["statusCode"] == 401
    assert bad_request("x")["statusCode"] == 400
    assert payment_required()["statusCode"] == 402
    body = json.loads(bad_request("nope", details={"f": 1})["body"])
    assert body["details"] == {"f": 1}


def test_cors_headers():
    r = ok({"x": 1})
    assert "Access-Control-Allow-Origin" in r["headers"]
