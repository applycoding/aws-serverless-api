"""Single-table access patterns against moto DynamoDB."""

import boto3
import pytest

from bookings.errors import ItemExists, RepositoryConflict
from bookings.models import BookingStatus
from bookings.repository import BookingRepository
from tests.helpers import seed_booking


def test_round_trip_stores_index_keys() -> None:
    booking = seed_booking(booking_id="bk-round", idempotency_key="round")
    repository = BookingRepository("bookings")

    assert repository.get("bk-round") == booking

    table = boto3.resource("dynamodb", region_name="us-east-1").Table("bookings")
    raw = table.get_item(Key={"PK": "BOOKING#bk-round", "SK": "META"})["Item"]
    assert raw["userPK"] == "USER#user-1"
    assert raw["statusPK"] == "STATUS#PENDING"
    assert raw["resourcePK"] == "RESOURCE#room-14"
    assert str(raw["userSK"]).startswith("START#")
    assert str(raw["statusSK"]).startswith("CREATED#")


def test_list_is_per_user_and_ordered_by_start() -> None:
    seed_booking(
        booking_id="later",
        user_id="user-1",
        start_time="2027-05-02T10:00:00Z",
        end_time="2027-05-02T11:00:00Z",
        idempotency_key="later",
    )
    seed_booking(
        booking_id="earlier",
        user_id="user-1",
        start_time="2027-05-01T10:00:00Z",
        end_time="2027-05-01T11:00:00Z",
        idempotency_key="earlier",
    )
    seed_booking(
        booking_id="other",
        user_id="user-2",
        start_time="2027-05-01T10:00:00Z",
        end_time="2027-05-01T11:00:00Z",
        idempotency_key="other",
    )

    bookings, truncated = BookingRepository("bookings").list_for_user("user-1", limit=10)

    assert [booking.booking_id for booking in bookings] == ["earlier", "later"]
    assert truncated is False


def test_pending_query_returns_only_old_pending_bookings() -> None:
    seed_booking(booking_id="old", created_at="2020-01-01T00:00:00Z", idempotency_key="old")
    seed_booking(booking_id="fresh", created_at="2099-01-01T00:00:00Z", idempotency_key="fresh")
    seed_booking(
        booking_id="done",
        status=BookingStatus.CONFIRMED,
        created_at="2020-01-01T00:00:00Z",
        idempotency_key="done",
    )

    found, truncated = BookingRepository("bookings").query_pending_created_before(
        "2026-01-01T00:00:00Z",
        limit=10,
    )

    assert [booking.booking_id for booking in found] == ["old"]
    assert truncated is False


def test_put_new_and_stale_status_are_rejected() -> None:
    booking = seed_booking(booking_id="once", idempotency_key="once")
    repository = BookingRepository("bookings")
    with pytest.raises(ItemExists):
        repository.put_new(booking)

    confirmed = booking.model_copy(update={"status": BookingStatus.CONFIRMED})
    repository.save_existing(confirmed, expected_status=BookingStatus.PENDING)
    cancelled = confirmed.model_copy(update={"status": BookingStatus.CANCELLED})
    with pytest.raises(RepositoryConflict):
        repository.save_existing(cancelled, expected_status=BookingStatus.PENDING)
