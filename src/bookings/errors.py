"""Domain errors raised by the booking service."""


class ItemExists(Exception):
    """A booking with this id is already stored."""


class RepositoryConflict(Exception):
    """The stored booking changed between the read and the write."""


class BookingNotFound(Exception):
    """No booking with this id belongs to the caller."""

    def __init__(self) -> None:
        super().__init__("Booking not found")


class BookingConflict(Exception):
    """The change clashes with the current booking state."""


class BookingValidationError(Exception):
    """The booking breaks a domain rule that schema validation cannot see."""


class EventPublishError(Exception):
    """EventBridge rejected the domain event."""
