"""Lazy boto3 clients so tests can install moto before the first call."""

import os

import boto3


def region_name() -> str:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"


def dynamodb_table(table_name: str):
    return boto3.resource("dynamodb", region_name=region_name()).Table(table_name)


def sqs_client():
    return boto3.client("sqs", region_name=region_name())


def events_client():
    return boto3.client("events", region_name=region_name())
