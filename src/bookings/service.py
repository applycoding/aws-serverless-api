"""Booking rules shared by the HTTP handlers, the confirmation worker, and cleanup.

Status moves in one direction:

    PENDING -> CONFIRMED   worker, while the slot is still in the future
    PENDING -> EXPIRED     worker if the slot has already started, or cleanup
                           if the booking is still pending after the timeout
    PENDING|CONFIRMED -> CANCELLED

CANCELLED and EXPIRED are terminal. Create is idempotent on
``{userId}#{Idempotency-Key}`` via AWS Lambda Powertools and a DynamoDB store.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Never

from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.idempotency import IdempotencyConfig, idempotent_function

from bookings.errors import (
    BookingConflict,
    BookingNotFound,
    BookingValidationError,
    ItemExists,
    RepositoryConflict,
)
from bookings.events import (
    BOOKING_CANCELLED,
    BOOKING_CONFIRMED,
    BOOKING_CREATED,
    BOOKING_EXPIRED,
    EventPublisher,
)
from bookings.idempotency import current_lambda_context, get_persistence_store
from bookings.models import Booking, BookingStatus, CreateBookingRequest, UpdateBookingRequest
from bookings.queue import ConfirmationQueue
from bookings.repository import BookingRepository
from bookings.timeutil import ensure_utc, parse_iso, to_iso

logger = Logger(child=True)
tracer = Tracer()

MAX_DURATION = timedelta(days=14)
LIST_LIMIT = 100
CLEANUP_LIMIT = 100
OVERLAP_LIMIT = 200
_BLOCKING = {BookingStatus.PENDING, BookingStatus.CONFIRMED}


class BookingService:
    def __init__(
        self,
        repository: BookingRepository,
        queue: ConfirmationQueue,
        events: EventPublisher,
        *,
        timeout_minutes: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if timeout_minutes < 1:
            raise ValueError("timeout_minutes must be positive")
        self._repository = repository
        self._queue = queue
        self._events = events
        self._timeout = timedelta(minutes=timeout_minutes)
        self._clock = clock or _utc_now
        self._idempotency_config: IdempotencyConfig | None = None
        self._create_once_idempotent: Callable[..., dict[str, Any]] | None = None

    @tracer.capture_method
    def create_booking(
        self,
        *,
        user_id: str,
        body: CreateBookingRequest,
        idempotency_key: str,
    ) -> Booking:
        request = {
            "idempotency_key": f"{user_id}#{idempotency_key}",
            "client_key": idempotency_key,
            "payload": {
                "userId": user_id,
                "resourceId": body.resource_id,
                "startTime": to_iso(ensure_utc(body.start_time)),
                "endTime": to_iso(ensure_utc(body.end_time)),
                "partySize": body.party_size,
                "notes": body.notes,
            },
        }
        stored = self._idempotent_create(request)
        booking = Booking.model_validate(stored)
        logger.info("Booking ready", booking_id=booking.booking_id, status=booking.status.value)
        return booking

    @tracer.capture_method
    def get_booking(self, user_id: str, booking_id: str) -> Booking:
        return self._owned(user_id, booking_id)

    @tracer.capture_method
    def list_bookings(self, user_id: str) -> tuple[list[Booking], bool]:
        return self._repository.list_for_user(user_id, limit=LIST_LIMIT)

    @tracer.capture_method
    def update_booking(
        self,
        user_id: str,
        booking_id: str,
        changes: UpdateBookingRequest,
    ) -> Booking:
        booking = self._owned(user_id, booking_id)
        if changes.is_empty:
            raise BookingValidationError("Provide at least one field to update")
        self._ensure_mutable(booking, changes)

        start = (
            parse_iso(booking.start_time)
            if changes.start_time is None
            else ensure_utc(changes.start_time)
        )
        end = (
            parse_iso(booking.end_time)
            if changes.end_time is None
            else ensure_utc(changes.end_time)
        )
        if changes.changes_times:
            self._validate_window(start, end, require_future=True)
            self._ensure_free(
                resource_id=booking.resource_id,
                start=start,
                end=end,
                ignore_booking_id=booking.booking_id,
            )

        updated = booking.model_copy(
            update={
                "start_time": to_iso(start),
                "end_time": to_iso(end),
                "party_size": (
                    booking.party_size if changes.party_size is None else changes.party_size
                ),
                "notes": booking.notes if changes.notes is None else changes.notes,
                "updated_at": to_iso(self._clock()),
            }
        )
        self._save(updated, expected_status=booking.status)
        logger.info("Booking updated", booking_id=updated.booking_id)
        return updated

    @tracer.capture_method
    def cancel_booking(self, user_id: str, booking_id: str) -> Booking:
        booking = self._owned(user_id, booking_id)
        match booking.status:
            case BookingStatus.CANCELLED:
                return booking
            case BookingStatus.EXPIRED:
                raise BookingConflict("Expired bookings cannot be cancelled")
            case BookingStatus.PENDING | BookingStatus.CONFIRMED:
                updated = self._transition(booking, BookingStatus.CANCELLED)
                self._events.publish(BOOKING_CANCELLED, updated.to_api())
                logger.info("Booking cancelled", booking_id=updated.booking_id)
                return updated
            case _ as unreachable:
                return _unexpected(unreachable)

    @tracer.capture_method
    def confirm_booking(self, booking_id: str, user_id: str) -> Booking:
        """Finish a queued confirmation. Terminal bookings are acknowledged and left as they are."""
        booking = self._repository.get(booking_id)
        if booking is None or booking.user_id != user_id:
            raise BookingNotFound
        match booking.status:
            case BookingStatus.CONFIRMED | BookingStatus.CANCELLED | BookingStatus.EXPIRED:
                return booking
            case BookingStatus.PENDING:
                if parse_iso(booking.start_time) <= self._clock():
                    updated = self._transition(booking, BookingStatus.EXPIRED)
                    self._events.publish(BOOKING_EXPIRED, updated.to_api())
                    logger.info("Pending booking expired at confirmation", booking_id=booking_id)
                    return updated
                updated = self._transition(booking, BookingStatus.CONFIRMED)
                self._events.publish(BOOKING_CONFIRMED, updated.to_api())
                logger.info("Booking confirmed", booking_id=booking_id)
                return updated
            case _ as unreachable:
                return _unexpected(unreachable)

    @tracer.capture_method
    def expire_stale(self) -> int:
        """Mark PENDING bookings created before the timeout as EXPIRED. One page per run."""
        cutoff = to_iso(self._clock() - self._timeout)
        pending, truncated = self._repository.query_pending_created_before(
            cutoff,
            limit=CLEANUP_LIMIT,
        )
        expired = 0
        for booking in pending:
            if booking.status is not BookingStatus.PENDING:
                continue
            updated = self._transition(booking, BookingStatus.EXPIRED)
            self._events.publish(BOOKING_EXPIRED, updated.to_api())
            expired += 1
        logger.info("Cleanup finished", expired=expired, truncated=truncated)
        return expired

    def _idempotent_create(self, request: dict[str, Any]) -> dict[str, Any]:
        if self._idempotency_config is None:
            self._idempotency_config = IdempotencyConfig(
                event_key_jmespath="idempotency_key",
                payload_validation_jmespath="payload",
                raise_on_no_idempotency_key=True,
                use_local_cache=False,
                expires_after_seconds=60 * 60 * 24,
            )
        context = current_lambda_context()
        if context is not None:
            self._idempotency_config.register_lambda_context(context)
        if self._create_once_idempotent is None:
            self._create_once_idempotent = idempotent_function(
                self._create_once,
                data_keyword_argument="request",
                persistence_store=get_persistence_store(),
                config=self._idempotency_config,
            )
        return self._create_once_idempotent(request=request)

    def _create_once(self, request: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = request["payload"]
        user_id = str(payload["userId"])
        start = parse_iso(str(payload["startTime"]))
        end = parse_iso(str(payload["endTime"]))
        self._validate_window(start, end, require_future=True)
        resource_id = str(payload["resourceId"])
        booking_id = _booking_id(user_id, str(request["client_key"]))
        self._ensure_free(
            resource_id=resource_id,
            start=start,
            end=end,
            ignore_booking_id=booking_id,
        )

        now = to_iso(self._clock())
        booking = Booking(
            booking_id=booking_id,
            user_id=user_id,
            resource_id=resource_id,
            start_time=str(payload["startTime"]),
            end_time=str(payload["endTime"]),
            status=BookingStatus.PENDING,
            party_size=int(payload["partySize"]),
            notes=str(payload["notes"]),
            created_at=now,
            updated_at=now,
            idempotency_key=str(request["client_key"]),
        )
        try:
            self._repository.put_new(booking)
        except ItemExists:
            return self._replay_existing(booking_id, user_id, payload)

        self._queue.enqueue(booking.booking_id, booking.user_id)
        self._events.publish(BOOKING_CREATED, booking.to_api())
        return booking.to_api()

    def _replay_existing(
        self,
        booking_id: str,
        user_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        existing = self._repository.get(booking_id)
        if existing is None or existing.user_id != user_id or not _same_payload(existing, payload):
            raise BookingConflict("Idempotency-Key was reused with a different request body")
        self._queue.enqueue(existing.booking_id, existing.user_id)
        self._events.publish(BOOKING_CREATED, existing.to_api())
        return existing.to_api()

    def _owned(self, user_id: str, booking_id: str) -> Booking:
        booking = self._repository.get(booking_id)
        if booking is None or booking.user_id != user_id:
            raise BookingNotFound
        return booking

    def _ensure_mutable(self, booking: Booking, changes: UpdateBookingRequest) -> None:
        match booking.status:
            case BookingStatus.CANCELLED | BookingStatus.EXPIRED:
                raise BookingConflict(f"Cannot update a {booking.status.value} booking")
            case BookingStatus.CONFIRMED:
                if changes.changes_times:
                    raise BookingConflict(
                        "Confirmed bookings only allow notes and partySize changes"
                    )
            case BookingStatus.PENDING:
                return
            case _ as unreachable:
                _unexpected(unreachable)

    def _validate_window(self, start: datetime, end: datetime, *, require_future: bool) -> None:
        if end <= start:
            raise BookingValidationError("endTime must be after startTime")
        if end - start > MAX_DURATION:
            raise BookingValidationError("Booking duration cannot exceed 14 days")
        if require_future and start <= self._clock():
            raise BookingValidationError("startTime must be in the future")

    def _ensure_free(
        self,
        *,
        resource_id: str,
        start: datetime,
        end: datetime,
        ignore_booking_id: str | None,
    ) -> None:
        window_start = to_iso(start - MAX_DURATION)
        window_end = to_iso(end)
        candidates, truncated = self._repository.query_resource_starting_between(
            resource_id,
            window_start,
            window_end,
            limit=OVERLAP_LIMIT,
        )
        if truncated:
            raise BookingValidationError("Unable to verify that the resource is free")
        for existing in candidates:
            if ignore_booking_id and existing.booking_id == ignore_booking_id:
                continue
            if existing.status not in _BLOCKING:
                continue
            if start < parse_iso(existing.end_time) and parse_iso(existing.start_time) < end:
                raise BookingConflict("Booking overlaps an existing reservation")

    def _transition(self, booking: Booking, status: BookingStatus) -> Booking:
        updated = booking.model_copy(update={"status": status, "updated_at": to_iso(self._clock())})
        self._save(updated, expected_status=booking.status)
        return updated

    def _save(self, booking: Booking, *, expected_status: BookingStatus) -> None:
        try:
            self._repository.save_existing(booking, expected_status=expected_status)
        except RepositoryConflict as exc:
            raise BookingConflict("Booking was updated concurrently") from exc


def _booking_id(user_id: str, idempotency_key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"booking:{user_id}:{idempotency_key}"))


def _same_payload(booking: Booking, payload: dict[str, Any]) -> bool:
    return (
        booking.resource_id == payload["resourceId"]
        and booking.start_time == payload["startTime"]
        and booking.end_time == payload["endTime"]
        and booking.party_size == payload["partySize"]
        and booking.notes == payload["notes"]
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _unexpected(status: Never) -> Never:
    raise AssertionError(f"Unhandled booking status: {status}")
