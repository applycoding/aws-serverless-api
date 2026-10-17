"""Schema rules that do not need AWS."""

import pytest
from pydantic import ValidationError

from bookings.models import CreateBookingRequest

VALID = {
    "resourceId": "room-14",
    "startTime": "2027-03-04T15:00:00Z",
    "endTime": "2027-03-04T16:00:00Z",
    "partySize": 4,
    "notes": "Window seat if you have one",
}


def test_create_request_accepts_a_normal_booking() -> None:
    booking = CreateBookingRequest.model_validate(VALID)
    assert booking.resource_id == "room-14"
    assert booking.party_size == 4


def test_create_request_rejects_a_reversed_window() -> None:
    body = {**VALID, "endTime": "2027-03-04T14:00:00Z"}
    with pytest.raises(ValidationError):
        CreateBookingRequest.model_validate(body)


def test_create_request_rejects_an_empty_party() -> None:
    body = {**VALID, "partySize": 0}
    with pytest.raises(ValidationError):
        CreateBookingRequest.model_validate(body)
