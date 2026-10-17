"""Moto backends for DynamoDB, SQS, and EventBridge. Schema mirrors template.yaml."""

import os

import boto3
import pytest
from moto import mock_aws

# Powertools Tracer reads this at import time. Set it before the application import.
os.environ["POWERTOOLS_TRACE_DISABLED"] = "true"
os.environ["POWERTOOLS_SERVICE_NAME"] = "bookings-api"
os.environ["POWERTOOLS_LOG_LEVEL"] = "WARNING"
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_SECURITY_TOKEN"] = "testing"
os.environ["AWS_SESSION_TOKEN"] = "testing"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["AWS_REGION"] = "us-east-1"
os.environ["AWS_EC2_METADATA_DISABLED"] = "true"

from bookings.handlers.runtime import reset_runtime  # noqa: E402

TABLE_NAME = "bookings"
IDEMPOTENCY_TABLE = "idempotency"
EVENT_BUS_NAME = "bookings"
REGION = "us-east-1"


@pytest.fixture(autouse=True)
def moto_aws():
    os.environ["TABLE_NAME"] = TABLE_NAME
    os.environ["IDEMPOTENCY_TABLE_NAME"] = IDEMPOTENCY_TABLE
    os.environ["EVENT_BUS_NAME"] = EVENT_BUS_NAME
    os.environ["CONFIRMATION_TIMEOUT_MINUTES"] = "30"
    with mock_aws():
        _create_bookings_table()
        _create_idempotency_table()
        queue_url = _create_queue()
        _create_bus()
        os.environ["CONFIRMATION_QUEUE_URL"] = queue_url
        reset_runtime()
        yield {"queue_url": queue_url, "bus_name": EVENT_BUS_NAME, "table_name": TABLE_NAME}
        reset_runtime()


def _create_bookings_table() -> None:
    boto3.client("dynamodb", region_name=REGION).create_table(
        TableName=TABLE_NAME,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            _attr("PK"),
            _attr("SK"),
            _attr("userPK"),
            _attr("userSK"),
            _attr("statusPK"),
            _attr("statusSK"),
            _attr("resourcePK"),
            _attr("resourceSK"),
        ],
        KeySchema=_keys("PK", "SK"),
        GlobalSecondaryIndexes=[
            _gsi("UserBookings", "userPK", "userSK"),
            _gsi("StatusCreated", "statusPK", "statusSK"),
            _gsi("ResourceSchedule", "resourcePK", "resourceSK"),
        ],
    )


def _create_idempotency_table() -> None:
    boto3.client("dynamodb", region_name=REGION).create_table(
        TableName=IDEMPOTENCY_TABLE,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[_attr("id")],
        KeySchema=_keys("id"),
    )


def _create_queue() -> str:
    response = boto3.client("sqs", region_name=REGION).create_queue(QueueName="confirmation")
    return response["QueueUrl"]


def _create_bus() -> None:
    boto3.client("events", region_name=REGION).create_event_bus(Name=EVENT_BUS_NAME)


def _attr(name: str) -> dict[str, str]:
    return {"AttributeName": name, "AttributeType": "S"}


def _keys(partition: str, sort: str | None = None) -> list[dict[str, str]]:
    schema = [{"AttributeName": partition, "KeyType": "HASH"}]
    if sort is not None:
        schema.append({"AttributeName": sort, "KeyType": "RANGE"})
    return schema


def _gsi(name: str, partition: str, sort: str) -> dict:
    return {
        "IndexName": name,
        "KeySchema": _keys(partition, sort),
        "Projection": {"ProjectionType": "ALL"},
    }
