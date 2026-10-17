"""SQS messages that ask the confirmation worker to finish a pending booking."""

import json
from typing import Any

from bookings.clients import sqs_client


class ConfirmationQueue:
    def __init__(self, queue_url: str, client: Any | None = None) -> None:
        self._queue_url = queue_url
        self._client = client or sqs_client()

    def enqueue(self, booking_id: str, user_id: str) -> None:
        self._client.send_message(
            QueueUrl=self._queue_url,
            MessageBody=json.dumps(
                {"bookingId": booking_id, "userId": user_id},
                separators=(",", ":"),
            ),
        )
