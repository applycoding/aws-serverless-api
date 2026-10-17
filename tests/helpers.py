"""HTTP API events and SQS records for handler tests."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3

from bookings.models import Booking, BookingStatus
from bookings.repository import BookingRepository
from bookings.timeutil import to_iso

REGION = "us-east-1"


class LambdaContext:
    function_name = "bookings-api"
    function_version = "$LATEST"
    invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:bookings-api"
    memory_limit_in_mb = 256
    aws_request_id = "req-test"
    log_group_name = "/aws/lambda/bookings-api"
    log_stream_name = "test"

    @staticmethod
    def get_remaining_time_in_millis() -> int:
        return 30_000


CONTEXT = LambdaContext()


def slot(
    *,
    start_offset: timedelta = timedelta(days=2),
    duration: timedelta = timedelta(hours=1),
) -> tuple[str, str]:
    start = datetime.now(UTC).replace(microsecond=0) + start_offset
    return to_iso(start), to_iso(start + duration)


def booking_body(
    resource_id: str = "room-14",
    *,
    start_offset: timedelta = timedelta(days=2),
    duration: timedelta = timedelta(hours=1),
    party_size: int = 2,
    notes: str = "Window light",
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    if start is None or end is None:
        start, end = slot(start_offset=start_offset, duration=duration)
    return {
        "resourceId": resource_id,
        "startTime": start,
        "endTime": end,
        "partySize": party_size,
        "notes": notes,
    }


def api_event(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    user_id: str | None = "user-1",
    headers: dict[str, str] | None = None,
    route_path: str | None = None,
) -> dict[str, Any]:
    header_map = {"content-type": "application/json"}
    if headers:
        header_map.update(headers)
    route = route_path or path
    event: dict[str, Any] = {
        "version": "2.0",
        "routeKey": f"{method} {route}",
        "rawPath": path,
        "rawQueryString": "",
        "headers": header_map,
        "requestContext": {
            "accountId": "123456789012",
            "apiId": "testapi",
            "requestId": "req-test",
            "routeKey": f"{method} {route}",
            "stage": "$default",
            "timeEpoch": 1_700_000_000_000,
            "http": {
                "method": method,
                "path": path,
                "protocol": "HTTP/1.1",
                "sourceIp": "127.0.0.1",
                "userAgent": "pytest",
            },
        },
        "isBase64Encoded": False,
    }
    if user_id is not None:
        event["requestContext"]["authorizer"] = {
            "jwt": {
                "claims": {"sub": user_id, "email": f"{user_id}@example.com"},
                "scopes": [],
            }
        }
    if body is not None:
        event["body"] = json.dumps(body)
    booking_id = _booking_id(path)
    if booking_id is not None:
        event["pathParameters"] = {"bookingId": booking_id}
        event["routeKey"] = f"{method} /bookings/{{bookingId}}"
        event["requestContext"]["routeKey"] = event["routeKey"]
    return event


def response_body(result: dict[str, Any]) -> dict[str, Any]:
    body = result.get("body")
    if not body:
        return {}
    return json.loads(body)


def drain_queue(queue_url: str) -> list[dict[str, Any]]:
    client = boto3.client("sqs", region_name=REGION)
    messages: list[dict[str, Any]] = []
    while True:
        response = client.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=0,
        )
        batch = response.get("Messages", [])
        if not batch:
            return messages
        messages.extend(batch)
        client.delete_message_batch(
            QueueUrl=queue_url,
            Entries=[
                {"Id": str(index), "ReceiptHandle": message["ReceiptHandle"]}
                for index, message in enumerate(batch)
            ],
        )


def sqs_event(records: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "Records": [
            {
                "messageId": message_id,
                "receiptHandle": f"handle-{message_id}",
                "body": body,
                "attributes": {
                    "ApproximateReceiveCount": "1",
                    "SentTimestamp": "0",
                    "SenderId": "sender",
                    "ApproximateFirstReceiveTimestamp": "0",
                },
                "messageAttributes": {},
                "md5OfBody": "d41d8cd98f00b204e9800998ecf8427e",
                "eventSource": "aws:sqs",
                "eventSourceARN": "arn:aws:sqs:us-east-1:123456789012:confirmation",
                "awsRegion": REGION,
            }
            for message_id, body in records
        ]
    }


def scheduled_event() -> dict[str, Any]:
    return {
        "version": "0",
        "id": "evt-cleanup",
        "detail-type": "Scheduled Event",
        "source": "aws.events",
        "account": "123456789012",
        "time": "2026-10-17T00:00:00Z",
        "region": REGION,
        "resources": ["arn:aws:events:us-east-1:123456789012:rule/expire-pending-bookings"],
        "detail": {},
    }


def seed_booking(**overrides: Any) -> Booking:
    start, end = slot(start_offset=timedelta(days=3))
    now = to_iso(datetime.now(UTC))
    values: dict[str, Any] = {
        "booking_id": "bk-seed",
        "user_id": "user-1",
        "resource_id": "room-14",
        "start_time": start,
        "end_time": end,
        "status": BookingStatus.PENDING,
        "party_size": 2,
        "notes": "",
        "created_at": now,
        "updated_at": now,
        "idempotency_key": "seed-key",
    }
    values.update(overrides)
    booking = Booking(**values)
    BookingRepository("bookings").put_new(booking)
    return booking


def _booking_id(path: str) -> str | None:
    prefix = "/bookings/"
    if not path.startswith(prefix):
        return None
    booking_id = path[len(prefix) :]
    return booking_id or None
