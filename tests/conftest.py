"""Shared pytest fixtures – moto DynamoDB single table."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

# Ensure backend package is importable
ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

TABLE_NAME = "stem-study-test"
REGION = "ap-southeast-1"


@pytest.fixture
def aws_credentials(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_REGION", REGION)
    monkeypatch.setenv("TABLE_NAME", TABLE_NAME)
    monkeypatch.setenv("ALLOW_TEST_AUTH", "1")
    # Clear db caches between tests
    from app import db

    db.clear_caches()


@pytest.fixture
def dynamodb_table(aws_credentials):
    with mock_aws():
        client = boto3.client("dynamodb", region_name=REGION)
        client.create_table(
            TableName=TABLE_NAME,
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": "GSI1PK", "AttributeType": "S"},
                {"AttributeName": "GSI1SK", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "GSI1",
                    "KeySchema": [
                        {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        from app import db

        db.clear_caches()
        yield boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)
        db.clear_caches()


@pytest.fixture
def user_headers():
    return {
        "x-test-user": "user-111",
        "x-test-email": "learner@example.com",
        "x-test-groups": "",
    }


@pytest.fixture
def admin_headers():
    return {
        "x-test-user": "admin-999",
        "x-test-email": "admin@example.com",
        "x-test-groups": "admin",
    }


from tests.helpers import make_event  # noqa: E402,F401  re-export
