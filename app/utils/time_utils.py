from __future__ import annotations

from datetime import datetime, timedelta, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def next_quarter_hour_utc(now: datetime | None = None) -> datetime:
    current = (now or utc_now()).astimezone(timezone.utc)
    minute_bucket = ((current.minute // 15) + 1) * 15
    if minute_bucket == 60:
        return current.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return current.replace(minute=minute_bucket, second=0, microsecond=0)


def next_top_of_hour_utc(now: datetime | None = None) -> datetime:
    current = (now or utc_now()).astimezone(timezone.utc)
    return current.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def parse_api_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def seconds_until(dt: datetime, now: datetime | None = None) -> int:
    current = (now or utc_now()).astimezone(timezone.utc)
    return int((dt.astimezone(timezone.utc) - current).total_seconds())
