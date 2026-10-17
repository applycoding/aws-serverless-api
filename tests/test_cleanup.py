"""Scheduled cleanup invoked the way EventBridge would invoke the Lambda."""

from bookings.handlers.cleanup import handler
from bookings.models import BookingStatus
from bookings.repository import BookingRepository
from tests.helpers import CONTEXT, scheduled_event, seed_booking


def test_cleanup_expires_only_stale_pending_bookings() -> None:
    seed_booking(booking_id="stale", created_at="2020-01-01T00:00:00Z", idempotency_key="stale")
    seed_booking(booking_id="fresh", created_at="2099-01-01T00:00:00Z", idempotency_key="fresh")
    seed_booking(
        booking_id="kept",
        status=BookingStatus.CONFIRMED,
        created_at="2020-01-01T00:00:00Z",
        idempotency_key="kept",
    )

    result = handler(scheduled_event(), CONTEXT)

    assert result == {"expired": 1}
    repository = BookingRepository("bookings")
    stale = repository.get("stale")
    fresh = repository.get("fresh")
    kept = repository.get("kept")
    assert stale is not None and stale.status is BookingStatus.EXPIRED
    assert fresh is not None and fresh.status is BookingStatus.PENDING
    assert kept is not None and kept.status is BookingStatus.CONFIRMED
