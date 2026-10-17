"""SQS worker. Confirms a pending booking, or expires it if the slot has started."""

from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.data_classes import SQSEvent, event_source
from aws_lambda_powertools.utilities.typing import LambdaContext

from bookings.handlers.runtime import get_service
from bookings.models import ConfirmationMessage

logger = Logger()
tracer = Tracer()


@logger.inject_lambda_context(clear_state=True)
@tracer.capture_lambda_handler
@event_source(data_class=SQSEvent)
def handler(event: SQSEvent, context: LambdaContext) -> dict[str, list[dict[str, str]]]:
    failures: list[dict[str, str]] = []
    for record in event.records:
        try:
            message = ConfirmationMessage.model_validate_json(record.body)
            get_service().confirm_booking(message.booking_id, message.user_id)
        except Exception:
            logger.exception("Confirmation failed", message_id=record.message_id)
            failures.append({"itemIdentifier": record.message_id})
    return {"batchItemFailures": failures}
