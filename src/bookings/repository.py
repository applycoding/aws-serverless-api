"""DynamoDB single-table access for bookings.

Base table
    PK = BOOKING#{bookingId}, SK = META. Get one booking.

UserBookings
    userPK = USER#{userId}, userSK = START#{startTime}#{bookingId}.
    List the caller's bookings in start-time order.

StatusCreated
    statusPK = STATUS#{status}, statusSK = CREATED#{createdAt}#{bookingId}.
    Find PENDING bookings older than the confirmation timeout.

ResourceSchedule
    resourcePK = RESOURCE#{resourceId}, resourceSK = START#{startTime}#{bookingId}.
    Look for overlapping holds on one resource.
"""

from typing import Any

from botocore.exceptions import ClientError

from bookings.clients import dynamodb_table
from bookings.errors import ItemExists, RepositoryConflict
from bookings.models import Booking, BookingStatus

USER_INDEX = "UserBookings"
STATUS_INDEX = "StatusCreated"
RESOURCE_INDEX = "ResourceSchedule"


class BookingRepository:
    def __init__(self, table_name: str) -> None:
        self._table = dynamodb_table(table_name)

    def put_new(self, booking: Booking) -> None:
        try:
            self._table.put_item(
                Item=booking.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as exc:
            if _conditional_failed(exc):
                raise ItemExists(booking.booking_id) from exc
            raise

    def save_existing(self, booking: Booking, expected_status: BookingStatus) -> None:
        try:
            self._table.put_item(
                Item=booking.to_item(),
                ConditionExpression=(
                    "attribute_exists(PK) AND #status = :expected AND userId = :user_id"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":expected": expected_status.value,
                    ":user_id": booking.user_id,
                },
            )
        except ClientError as exc:
            if _conditional_failed(exc):
                raise RepositoryConflict(booking.booking_id) from exc
            raise

    def get(self, booking_id: str) -> Booking | None:
        response = self._table.get_item(
            Key={"PK": f"BOOKING#{booking_id}", "SK": "META"},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if item is None:
            return None
        return Booking.from_item(item)

    def list_for_user(self, user_id: str, *, limit: int) -> tuple[list[Booking], bool]:
        items, truncated = self._query(
            index_name=USER_INDEX,
            key_expression="userPK = :pk",
            values={":pk": f"USER#{user_id}"},
            limit=limit,
        )
        return [Booking.from_item(item) for item in items], truncated

    def query_pending_created_before(
        self,
        cutoff_iso: str,
        *,
        limit: int,
    ) -> tuple[list[Booking], bool]:
        items, truncated = self._query(
            index_name=STATUS_INDEX,
            key_expression="statusPK = :pk AND statusSK < :cutoff",
            values={":pk": "STATUS#PENDING", ":cutoff": f"CREATED#{cutoff_iso}"},
            limit=limit,
        )
        return [Booking.from_item(item) for item in items], truncated

    def query_resource_starting_between(
        self,
        resource_id: str,
        start_iso: str,
        end_iso: str,
        *,
        limit: int,
    ) -> tuple[list[Booking], bool]:
        """Bookings on a resource whose start falls in the inclusive ISO window."""
        items, truncated = self._query(
            index_name=RESOURCE_INDEX,
            key_expression="resourcePK = :pk AND resourceSK BETWEEN :low AND :high",
            values={
                ":pk": f"RESOURCE#{resource_id}",
                ":low": f"START#{start_iso}",
                ":high": f"START#{end_iso}~",
            },
            limit=limit,
        )
        return [Booking.from_item(item) for item in items], truncated

    def _query(
        self,
        *,
        index_name: str,
        key_expression: str,
        values: dict[str, str],
        limit: int,
    ) -> tuple[list[dict[str, Any]], bool]:
        items: list[dict[str, Any]] = []
        start_key: dict[str, Any] | None = None
        while len(items) < limit:
            params: dict[str, Any] = {
                "IndexName": index_name,
                "KeyConditionExpression": key_expression,
                "ExpressionAttributeValues": values,
                "Limit": limit - len(items),
            }
            if start_key is not None:
                params["ExclusiveStartKey"] = start_key
            response = self._table.query(**params)
            items.extend(response.get("Items", []))
            start_key = response.get("LastEvaluatedKey")
            if not start_key:
                return items, False
        return items, True


def _conditional_failed(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"
