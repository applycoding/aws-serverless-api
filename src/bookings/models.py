"""Request and item models for a resource booking."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BookingStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class CreateBookingRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    resource_id: str = Field(
        alias="resourceId",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    start_time: datetime = Field(alias="startTime")
    end_time: datetime = Field(alias="endTime")
    party_size: int = Field(alias="partySize", ge=1, le=20)
    notes: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def end_after_start(self) -> "CreateBookingRequest":
        if self.end_time <= self.start_time:
            raise ValueError("endTime must be after startTime")
        return self


class UpdateBookingRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    start_time: datetime | None = Field(default=None, alias="startTime")
    end_time: datetime | None = Field(default=None, alias="endTime")
    party_size: int | None = Field(default=None, alias="partySize", ge=1, le=20)
    notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def end_after_start_when_both_sent(self) -> "UpdateBookingRequest":
        if (
            self.start_time is not None
            and self.end_time is not None
            and self.end_time <= self.start_time
        ):
            raise ValueError("endTime must be after startTime")
        return self

    @property
    def changes_times(self) -> bool:
        return self.start_time is not None or self.end_time is not None

    @property
    def is_empty(self) -> bool:
        return (
            self.start_time is None
            and self.end_time is None
            and self.party_size is None
            and self.notes is None
        )


class Booking(BaseModel):
    """One booking item. Key attributes are derived in ``to_item`` and are not stored here."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore", frozen=True)

    booking_id: str = Field(alias="bookingId")
    user_id: str = Field(alias="userId")
    resource_id: str = Field(alias="resourceId")
    start_time: str = Field(alias="startTime")
    end_time: str = Field(alias="endTime")
    status: BookingStatus
    party_size: int = Field(alias="partySize")
    notes: str
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")
    idempotency_key: str = Field(alias="idempotencyKey")

    def to_api(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")

    def to_item(self) -> dict[str, Any]:
        """Single-table item. Indexes are maintained by rewriting the whole item."""
        return {
            "PK": f"BOOKING#{self.booking_id}",
            "SK": "META",
            "userPK": f"USER#{self.user_id}",
            "userSK": f"START#{self.start_time}#{self.booking_id}",
            "statusPK": f"STATUS#{self.status.value}",
            "statusSK": f"CREATED#{self.created_at}#{self.booking_id}",
            "resourcePK": f"RESOURCE#{self.resource_id}",
            "resourceSK": f"START#{self.start_time}#{self.booking_id}",
            "entityType": "BOOKING",
            "bookingId": self.booking_id,
            "userId": self.user_id,
            "resourceId": self.resource_id,
            "startTime": self.start_time,
            "endTime": self.end_time,
            "status": self.status.value,
            "partySize": self.party_size,
            "notes": self.notes,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "idempotencyKey": self.idempotency_key,
        }

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "Booking":
        return cls(
            booking_id=str(item["bookingId"]),
            user_id=str(item["userId"]),
            resource_id=str(item["resourceId"]),
            start_time=str(item["startTime"]),
            end_time=str(item["endTime"]),
            status=BookingStatus(str(item["status"])),
            party_size=int(item["partySize"]),
            notes=str(item.get("notes", "")),
            created_at=str(item["createdAt"]),
            updated_at=str(item["updatedAt"]),
            idempotency_key=str(item["idempotencyKey"]),
        )


class ConfirmationMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    booking_id: str = Field(alias="bookingId", min_length=1)
    user_id: str = Field(alias="userId", min_length=1)
