"""Shared service wiring. Clients are created on first use, not at import."""

import os
from functools import lru_cache

from bookings.events import EventPublisher
from bookings.idempotency import reset_persistence_store
from bookings.queue import ConfirmationQueue
from bookings.repository import BookingRepository
from bookings.service import BookingService


@lru_cache(maxsize=1)
def get_service() -> BookingService:
    timeout = int(os.environ.get("CONFIRMATION_TIMEOUT_MINUTES", "30"))
    return BookingService(
        repository=BookingRepository(os.environ["TABLE_NAME"]),
        queue=ConfirmationQueue(os.environ["CONFIRMATION_QUEUE_URL"]),
        events=EventPublisher(os.environ["EVENT_BUS_NAME"]),
        timeout_minutes=timeout,
    )


def reset_runtime() -> None:
    get_service.cache_clear()
    reset_persistence_store()
