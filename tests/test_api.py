"""HTTP handler tests. Cognito is represented by the JWT claims API Gateway would inject."""

from datetime import timedelta

import boto3

from bookings.handlers.api import handler as api_handler
from bookings.handlers.confirm import handler as confirm_handler
from bookings.timeutil import parse_iso, to_iso
from tests.helpers import (
    CONTEXT,
    api_event,
    booking_body,
    drain_queue,
    response_body,
    slot,
    sqs_event,
)


def test_health_does_not_require_a_user() -> None:
    result = api_handler(api_event("GET", "/health", user_id=None), CONTEXT)
    assert result["statusCode"] == 200
    assert response_body(result) == {"status": "ok"}


def test_missing_jwt_is_rejected() -> None:
    result = api_handler(api_event("GET", "/bookings", user_id=None), CONTEXT)
    assert result["statusCode"] == 401
    assert "token" in response_body(result)["message"]


def test_invalid_body_is_rejected() -> None:
    event = api_event("POST", "/bookings", headers={"idempotency-key": "key-1"})
    event["body"] = "{"
    result = api_handler(event, CONTEXT)
    assert result["statusCode"] == 400

    event = api_event(
        "POST",
        "/bookings",
        body={"resourceId": "room-14", "partySize": 0},
        headers={"idempotency-key": "key-1"},
    )
    result = api_handler(event, CONTEXT)
    assert result["statusCode"] == 422
    assert response_body(result)["errors"]


def test_idempotency_key_is_required() -> None:
    result = api_handler(api_event("POST", "/bookings", body=booking_body()), CONTEXT)
    assert result["statusCode"] == 400

    result = api_handler(
        api_event(
            "POST",
            "/bookings",
            body=booking_body(),
            headers={"idempotency-key": "not a key"},
        ),
        CONTEXT,
    )
    assert result["statusCode"] == 400


def test_create_is_idempotent_and_enqueues_once(moto_aws: dict) -> None:
    body = booking_body()
    headers = {"idempotency-key": "create-once"}
    first = api_handler(api_event("POST", "/bookings", body=body, headers=headers), CONTEXT)
    second = api_handler(api_event("POST", "/bookings", body=body, headers=headers), CONTEXT)

    assert first["statusCode"] == 201
    assert second["statusCode"] == 201
    created = response_body(first)
    assert created["bookingId"] == response_body(second)["bookingId"]
    assert created["status"] == "PENDING"

    messages = drain_queue(moto_aws["queue_url"])
    assert len(messages) == 1
    stored = boto3.client("dynamodb", region_name="us-east-1").scan(TableName="idempotency")
    assert stored["Count"] == 1

    listed = response_body(api_handler(api_event("GET", "/bookings"), CONTEXT))
    assert listed["truncated"] is False
    assert len(listed["bookings"]) == 1


def test_reused_key_with_a_different_body_conflicts() -> None:
    headers = {"idempotency-key": "same-key"}
    first = api_handler(
        api_event("POST", "/bookings", body=booking_body(), headers=headers),
        CONTEXT,
    )
    second = api_handler(
        api_event(
            "POST",
            "/bookings",
            body=booking_body(resource_id="room-15"),
            headers=headers,
        ),
        CONTEXT,
    )
    assert first["statusCode"] == 201
    assert second["statusCode"] == 409


def test_overlapping_hold_on_the_same_resource_conflicts() -> None:
    body = booking_body(start_offset=timedelta(days=4))
    first = api_handler(
        api_event(
            "POST",
            "/bookings",
            body=body,
            user_id="user-1",
            headers={"idempotency-key": "hold-a"},
        ),
        CONTEXT,
    )
    overlap = api_handler(
        api_event(
            "POST",
            "/bookings",
            body=body,
            user_id="user-2",
            headers={"idempotency-key": "hold-b"},
        ),
        CONTEXT,
    )
    other_resource = api_handler(
        api_event(
            "POST",
            "/bookings",
            body=booking_body(resource_id="room-99", start=body["startTime"], end=body["endTime"]),
            user_id="user-2",
            headers={"idempotency-key": "hold-c"},
        ),
        CONTEXT,
    )
    assert first["statusCode"] == 201
    assert overlap["statusCode"] == 409
    assert other_resource["statusCode"] == 201


def test_adjacent_slots_are_allowed() -> None:
    start, middle = slot(start_offset=timedelta(days=5))
    end = to_iso(parse_iso(middle) + timedelta(hours=1))
    first = booking_body(start=start, end=middle)
    second = booking_body(start=middle, end=end)
    assert (
        api_handler(
            api_event("POST", "/bookings", body=first, headers={"idempotency-key": "adj-1"}),
            CONTEXT,
        )["statusCode"]
        == 201
    )
    assert (
        api_handler(
            api_event("POST", "/bookings", body=second, headers={"idempotency-key": "adj-2"}),
            CONTEXT,
        )["statusCode"]
        == 201
    )


def test_past_start_and_long_duration_are_rejected() -> None:
    past = api_handler(
        api_event(
            "POST",
            "/bookings",
            body=booking_body(start="2020-01-01T10:00:00Z", end="2020-01-01T11:00:00Z"),
            headers={"idempotency-key": "past"},
        ),
        CONTEXT,
    )
    long = api_handler(
        api_event(
            "POST",
            "/bookings",
            body=booking_body(duration=timedelta(days=15)),
            headers={"idempotency-key": "long"},
        ),
        CONTEXT,
    )
    assert past["statusCode"] == 422
    assert "future" in response_body(past)["message"]
    assert long["statusCode"] == 422
    assert "14 days" in response_body(long)["message"]


def test_callers_cannot_read_someone_elses_booking() -> None:
    created = response_body(
        api_handler(
            api_event(
                "POST",
                "/bookings",
                body=booking_body(),
                user_id="user-1",
                headers={"idempotency-key": "private"},
            ),
            CONTEXT,
        )
    )
    hidden = api_handler(
        api_event("GET", f"/bookings/{created['bookingId']}", user_id="user-2"),
        CONTEXT,
    )
    assert hidden["statusCode"] == 404
    visible = api_handler(
        api_event("GET", f"/bookings/{created['bookingId']}", user_id="user-1"),
        CONTEXT,
    )
    assert visible["statusCode"] == 200


def test_lifecycle_confirms_then_cancels(moto_aws: dict) -> None:
    body = booking_body(start_offset=timedelta(days=6))
    created = response_body(
        api_handler(
            api_event("POST", "/bookings", body=body, headers={"idempotency-key": "life"}),
            CONTEXT,
        )
    )
    booking_id = created["bookingId"]
    messages = drain_queue(moto_aws["queue_url"])
    assert len(messages) == 1

    result = confirm_handler(sqs_event([("m-life", messages[0]["Body"])]), CONTEXT)
    assert result["batchItemFailures"] == []

    confirmed = response_body(api_handler(api_event("GET", f"/bookings/{booking_id}"), CONTEXT))
    assert confirmed["status"] == "CONFIRMED"

    noted = response_body(
        api_handler(
            api_event("PUT", f"/bookings/{booking_id}", body={"notes": "Corner table"}),
            CONTEXT,
        )
    )
    assert noted["notes"] == "Corner table"
    moved = api_handler(
        api_event(
            "PUT",
            f"/bookings/{booking_id}",
            body={"startTime": "2027-08-01T15:00:00Z", "endTime": "2027-08-01T16:00:00Z"},
        ),
        CONTEXT,
    )
    assert moved["statusCode"] == 409

    cancelled = response_body(api_handler(api_event("DELETE", f"/bookings/{booking_id}"), CONTEXT))
    assert cancelled["status"] == "CANCELLED"
    again = response_body(api_handler(api_event("DELETE", f"/bookings/{booking_id}"), CONTEXT))
    assert again["status"] == "CANCELLED"

    rebooked = api_handler(
        api_event("POST", "/bookings", body=body, headers={"idempotency-key": "life-2"}),
        CONTEXT,
    )
    assert rebooked["statusCode"] == 201
