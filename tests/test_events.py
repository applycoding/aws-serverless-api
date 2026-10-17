"""EventBridge put_events delivered to an SQS rule target under moto."""

import json

import boto3

from bookings.events import BOOKING_CREATED, EventPublisher

REGION = "us-east-1"


def test_booking_created_reaches_a_queue_target(moto_aws: dict) -> None:
    sqs = boto3.client("sqs", region_name=REGION)
    events = boto3.client("events", region_name=REGION)
    queue_url = sqs.create_queue(QueueName="booking-created-log")["QueueUrl"]
    queue_arn = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])[
        "Attributes"
    ]["QueueArn"]
    events.put_rule(
        Name="booking-created",
        EventBusName=moto_aws["bus_name"],
        EventPattern=json.dumps({"source": ["bookings.api"], "detail-type": [BOOKING_CREATED]}),
        State="ENABLED",
    )
    events.put_targets(
        Rule="booking-created",
        EventBusName=moto_aws["bus_name"],
        Targets=[{"Id": "log", "Arn": queue_arn}],
    )

    EventPublisher(moto_aws["bus_name"]).publish(
        BOOKING_CREATED,
        {"bookingId": "bk-event", "status": "PENDING"},
    )

    messages = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=0)
    body = json.loads(messages["Messages"][0]["Body"])
    assert body["source"] == "bookings.api"
    assert body["detail-type"] == BOOKING_CREATED
    assert body["detail"]["bookingId"] == "bk-event"
