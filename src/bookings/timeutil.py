"""UTC timestamps stored as sortable ISO-8601 strings."""

from datetime import UTC, datetime


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def to_iso(value: datetime) -> str:
    """Format UTC time at second precision so DynamoDB sort keys compare correctly."""
    return ensure_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    return ensure_utc(datetime.fromisoformat(normalized))
