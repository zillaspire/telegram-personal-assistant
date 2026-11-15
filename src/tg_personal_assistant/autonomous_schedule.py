from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_SCHEDULE = {
    "enabled": False, "start": "18:00", "end": "23:00",
    "timezone": "Europe/Kyiv", "days": list(DAYS),
}


def parse_hour(value: str) -> time:
    hour = time.fromisoformat(value)
    if hour.isoformat(timespec="minutes") != value:
        raise ValueError("Expected HH:MM")
    return hour


def _boundary(day: date, hour: time, zone: ZoneInfo, *, end: bool) -> float:
    local = datetime.combine(day, hour)
    # A skipped wall-clock time moves to the first existing minute.
    for _ in range(1441):
        choices = [
            instant.timestamp()
            for fold in (0, 1)
            if (instant := local.replace(tzinfo=zone, fold=fold).astimezone(UTC))
            .astimezone(zone).replace(tzinfo=None) == local
        ]
        if choices:
            return max(choices) if end else min(choices)
        local += timedelta(minutes=1)
    raise ValueError("Schedule boundary cannot be resolved")


def current_window(schedule: dict, now: float) -> tuple[float, float] | None:
    if not schedule["enabled"]:
        return None
    zone = ZoneInfo(schedule["timezone"])
    today = datetime.fromtimestamp(now, zone).date()
    start, end = parse_hour(schedule["start"]), parse_hour(schedule["end"])
    for day in (today, today - timedelta(days=1)):
        if DAYS[day.weekday()] not in schedule["days"]:
            continue
        finish_day = day + timedelta(days=int(end < start))
        opening = _boundary(day, start, zone, end=False)
        closing = _boundary(finish_day, end, zone, end=True)
        if opening <= now < closing:
            return opening, closing
    return None
