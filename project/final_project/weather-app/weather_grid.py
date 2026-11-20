"""Aggregate NWS gridded precipitation amounts into local forecast windows."""

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from precipitation import rain_category, snow_category


def parse_duration(value):
    """Parse NWS ISO durations such as PT3H, PT6H, P1DT3H."""
    match = re.fullmatch(
        r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?",
        value,
    )
    if not match:
        raise ValueError("Unsupported NWS duration: " + value)

    days, hours, minutes = (
        int(x or 0) for x in match.groups()
    )
    duration = timedelta(days=days, hours=hours, minutes=minutes)
    if duration <= timedelta(0):
        raise ValueError("Duration must be positive")
    return duration


def parse_interval(value):
    start_text, duration_text = value.split("/", 1)
    start = datetime.fromisoformat(
        start_text.replace("Z", "+00:00")
    )
    if start.tzinfo is None:
        raise ValueError("NWS interval has no timezone")
    return start, start + parse_duration(duration_text)


def average_rate(field, window_start, window_end, unit):
    """Return average rate, or None if window coverage is incomplete.

    Assumes the NWS forecast amount is uniformly distributed
    across each validTime interval.
    """
    if not isinstance(field, dict) or field.get("uom") != unit:
        return None

    pieces = []

    for entry in field.get("values", []):
        amount = entry.get("value")
        if isinstance(amount, bool) or not isinstance(
            amount, (int, float)
        ) or amount < 0:
            continue

        try:
            start, end = parse_interval(entry["validTime"])
        except (KeyError, TypeError, ValueError):
            continue

        overlap_start = max(start, window_start)
        overlap_end = min(end, window_end)

        if overlap_end <= overlap_start:
            continue

        interval_hours = (end - start).total_seconds() / 3600
        overlap_hours = (
            overlap_end - overlap_start
        ).total_seconds() / 3600

        pieces.append((
            overlap_start,
            overlap_end,
            amount / interval_hours * overlap_hours,
        ))

    if not pieces:
        return None

    pieces.sort(key=lambda x: x[0])
    cursor = window_start
    total_amount = 0.0

    for start, end, amount in pieces:
        if start != cursor:
            return None
        total_amount += amount
        cursor = end

    if cursor != window_end:
        return None

    hours = (window_end - window_start).total_seconds() / 3600
    return round(total_amount / hours, 3)


def aggregate_grid(properties, selected_date, timezone_name):
    day = date.fromisoformat(selected_date)
    zone = ZoneInfo(timezone_name)

    result = {}

    for name, start_hour, end_hour in (
        ("morning", 6, 12),
        ("night", 18, 24),
    ):
        start = datetime(
            day.year, day.month, day.day,
            start_hour, tzinfo=zone,
        )
        end = datetime(
            day.year, day.month, day.day,
            0, tzinfo=zone,
        ) + timedelta(hours=end_hour)

        rain_equivalent = average_rate(
            properties.get("quantitativePrecipitation"),
            start, end, "wmoUnit:mm",
        )
        snowfall_mm = average_rate(
            properties.get("snowfallAmount"),
            start, end, "wmoUnit:mm",
        )

        # QPF includes snow liquid equivalent. A positive QPF
        # alone cannot establish a rain event.
        rain = (
            "No" if rain_equivalent == 0
            else "Not available"
        )

        snow = snow_category(
            snowfall_mm / 10 if snowfall_mm is not None else None
        )

        result[name] = {
            "precipitation_equivalent_rate_mm_h": rain_equivalent,
            "snowfall_rate_cm_h": (
                round(snowfall_mm / 10, 3)
                if snowfall_mm is not None else None
            ),
            "rain_category": rain,
            "snow_category": snow,
        }

    return result
