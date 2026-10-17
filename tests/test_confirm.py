"""Confirmation worker. The queue event is the Lambda contract, backed by moto DynamoDB."""

from bookings.handlers.confirm import handler
from bookings.models import BookingStatus
from bookings.repository import BookingRepository
from tests.helpers import CONTEXT, seed_booking, sqs_event


def test_invalid_message_fails_without_blocking_a_valid_one() -> None:
    booking = seed_booking(booking_id="bk-ok", idempotency_key="ok")
    body = f'{{"bookingId":"{booking.booking_id}","userId":"{booking.user_id}"}}'
    result = handler(sqs_event([("bad", "{"), ("good", body)]), CONTEXT)

    assert result["batchItemFailures"] == [{"itemIdentifier": "bad"}]
    stored = BookingRepository("bookings").get("bk-ok")
    assert stored is not None
    assert stored.status is BookingStatus.CONFIRMED


def test_worker_expires_a_pending_booking_whose_slot_has_started() -> None:
    booking = seed_booking(
        booking_id="bk-late",
        start_time="2020-06-01T10:00:00Z",
        end_time="2020-06-01T11:00:00Z",
        idempotency_key="late",
    )
    body = f'{{"bookingId":"{booking.booking_id}","userId":"{booking.user_id}"}}'
    result = handler(sqs_event([("late", body)]), CONTEXT)

    assert result["batchItemFailures"] == []
    stored = BookingRepository("bookings").get("bk-late")
    assert stored is not None
    assert stored.status is BookingStatus.EXPIRED
