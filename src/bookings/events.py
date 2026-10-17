"""Domain events published to the bookings EventBridge bus."""

import json
from typing import Any

from bookings.clients import events_client
from bookings.errors import EventPublishError

SOURCE = "bookings.api"
BOOKING_CREATED = "BookingCreated"
BOOKING_CONFIRMED = "BookingConfirmed"
BOOKING_CANCELLED = "BookingCancelled"
BOOKING_EXPIRED = "BookingExpired"


class EventPublisher:
    def __init__(self, bus_name: str, client: Any | None = None) -> None:
        self._bus_name = bus_name
        self._client = client or events_client()

    def publish(self, detail_type: str, detail: dict[str, Any]) -> None:
        response = self._client.put_events(
            Entries=[
                {
                    "Source": SOURCE,
                    "DetailType": detail_type,
                    "Detail": json.dumps(detail, separators=(",", ":"), sort_keys=True),
                    "EventBusName": self._bus_name,
                }
            ]
        )
        failed = int(response.get("FailedEntryCount", 0))
        if failed:
            entry = response.get("Entries", [{}])[0]
            message = entry.get("ErrorMessage") or "EventBridge put_events failed"
            raise EventPublishError(message)
