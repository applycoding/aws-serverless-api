"""EventBridge schedule target. Expires pending bookings past the confirmation timeout."""

from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.data_classes import EventBridgeEvent, event_source
from aws_lambda_powertools.utilities.typing import LambdaContext

from bookings.handlers.runtime import get_service

logger = Logger()
tracer = Tracer()


@logger.inject_lambda_context(clear_state=True)
@tracer.capture_lambda_handler
@event_source(data_class=EventBridgeEvent)
def handler(event: EventBridgeEvent, context: LambdaContext) -> dict[str, int]:
    expired = get_service().expire_stale()
    logger.info("Scheduled cleanup complete", expired=expired, detail_type=event.detail_type)
    return {"expired": expired}
