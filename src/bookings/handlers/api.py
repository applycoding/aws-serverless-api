"""HTTP API Lambda. API Gateway authorizes the Cognito JWT before this runs."""

import json
import re
from typing import Any

from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver, Response, content_types
from aws_lambda_powertools.event_handler.exceptions import BadRequestError, UnauthorizedError
from aws_lambda_powertools.utilities.idempotency.exceptions import IdempotencyValidationError
from aws_lambda_powertools.utilities.typing import LambdaContext
from pydantic import ValidationError

from bookings.errors import BookingConflict, BookingNotFound, BookingValidationError
from bookings.handlers.runtime import get_service
from bookings.idempotency import set_lambda_context
from bookings.models import CreateBookingRequest, UpdateBookingRequest

logger = Logger()
tracer = Tracer()
app = APIGatewayHttpResolver()

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


@app.get("/health")
@tracer.capture_method
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/bookings")
@tracer.capture_method
def create_booking() -> tuple[dict[str, Any], int]:
    body = CreateBookingRequest.model_validate(_json_object())
    booking = get_service().create_booking(
        user_id=_user_id(),
        body=body,
        idempotency_key=_idempotency_key(),
    )
    return booking.to_api(), 201


@app.get("/bookings")
@tracer.capture_method
def list_bookings() -> dict[str, Any]:
    bookings, truncated = get_service().list_bookings(_user_id())
    return {"bookings": [booking.to_api() for booking in bookings], "truncated": truncated}


@app.get("/bookings/<bookingId>")
@tracer.capture_method
def get_booking(bookingId: str) -> dict[str, Any]:
    return get_service().get_booking(_user_id(), bookingId).to_api()


@app.put("/bookings/<bookingId>")
@tracer.capture_method
def update_booking(bookingId: str) -> dict[str, Any]:
    changes = UpdateBookingRequest.model_validate(_json_object())
    return get_service().update_booking(_user_id(), bookingId, changes).to_api()


@app.delete("/bookings/<bookingId>")
@tracer.capture_method
def cancel_booking(bookingId: str) -> dict[str, Any]:
    return get_service().cancel_booking(_user_id(), bookingId).to_api()


@app.exception_handler(BookingNotFound)
def booking_not_found(exc: BookingNotFound) -> Response:
    return _error(404, str(exc))


@app.exception_handler(BookingConflict)
def booking_conflict(exc: BookingConflict) -> Response:
    return _error(409, str(exc))


@app.exception_handler(BookingValidationError)
def booking_invalid(exc: BookingValidationError) -> Response:
    return _error(422, str(exc))


@app.exception_handler(IdempotencyValidationError)
def idempotency_mismatch(_exc: IdempotencyValidationError) -> Response:
    return _error(409, "Idempotency-Key was reused with a different request body")


@app.exception_handler(ValidationError)
def invalid_body(exc: ValidationError) -> Response:
    errors = [
        {"loc": [str(part) for part in error.get("loc", ())], "msg": str(error.get("msg", ""))}
        for error in exc.errors()
    ]
    return _error(422, "Validation failed", errors=errors)


@logger.inject_lambda_context(clear_state=True)
@tracer.capture_lambda_handler
def handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    set_lambda_context(context)
    return app.resolve(event, context)


def _user_id() -> str:
    raw = app.current_event.raw_event
    try:
        subject = raw["requestContext"]["authorizer"]["jwt"]["claims"]["sub"]
    except (KeyError, TypeError) as exc:
        raise UnauthorizedError("A valid Cognito access token is required") from exc
    if not isinstance(subject, str) or not subject.strip():
        raise UnauthorizedError("A valid Cognito access token is required")
    return subject


def _idempotency_key() -> str:
    value = _header("idempotency-key")
    if value is None or not value.strip():
        raise BadRequestError("Idempotency-Key header is required")
    key = value.strip()
    if _IDEMPOTENCY_KEY.fullmatch(key) is None:
        raise BadRequestError(
            "Idempotency-Key must be 1-128 characters and use only letters, numbers, "
            "underscores, and hyphens"
        )
    return key


def _json_object() -> dict[str, Any]:
    raw = app.current_event.body
    if raw is None or not raw.strip():
        raise BadRequestError("Request body is required")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BadRequestError("Request body must be JSON") from exc
    if not isinstance(parsed, dict):
        raise BadRequestError("Request body must be a JSON object")
    return parsed


def _header(name: str) -> str | None:
    value = app.current_event.headers.get(name)
    if value is None:
        return None
    if isinstance(value, list):
        return str(value[0]) if value else None
    return str(value)


def _error(status: int, message: str, *, errors: list[dict[str, Any]] | None = None) -> Response:
    body: dict[str, Any] = {"message": message}
    if errors is not None:
        body["errors"] = errors
    return Response(status_code=status, content_type=content_types.APPLICATION_JSON, body=body)
