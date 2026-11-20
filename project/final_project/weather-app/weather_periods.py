"""Pure hourly-forecast aggregation for local morning/night reporting."""
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

WINDOWS = {"morning": range(6, 12), "night": range(18, 24)}




def wind_mph(value):
    """Convert NWS wind speed to mph; return None for unknown values."""
    if not isinstance(value, str):
        return None

    match = re.fullmatch(
        r"\s*(\d+(?:\.\d+)?)"
        r"(?:\s+to\s+(\d+(?:\.\d+)?))?"
        r"\s*(mph|km/h)\s*",
        value,
        re.IGNORECASE,
    )

    if not match:
        return None

    low = float(match.group(1))
    high = float(match.group(2)) if match.group(2) else low

    if high < low:
        return None

    speed = (low + high) / 2

    if match.group(3).lower() == "km/h":
        speed /= 1.609344

    return round(speed, 1)


def aggregate_hourly(periods, selected_date, timezone_name):
    """Summarize hourly NWS periods; absent values remain None."""
    day = date.fromisoformat(selected_date)
    zone = ZoneInfo(timezone_name)
    buckets = {name: [] for name in WINDOWS}
    for period in periods:
        start = datetime.fromisoformat(period["startTime"].replace("Z", "+00:00"))
        if start.tzinfo is None:
            raise ValueError("NWS startTime must have a timezone")
        local = start.astimezone(zone)
        if local.date() != day:
            continue
        for name, hours in WINDOWS.items():
            if local.hour in hours:
                buckets[name].append(period)

    def mean(values):
        nums = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
        return round(sum(nums) / len(nums), 1) if nums else None

    def nested(period, key):
        value = period.get(key)
        return value.get("value") if isinstance(value, dict) else None

    result = {}
    for name, entries in buckets.items():
        result[name] = {
            "hours_available": len(entries),
            "temperature_f": mean([p.get("temperature") for p in entries]),
            "relative_humidity_percent": mean([nested(p, "relativeHumidity") for p in entries]),
            "precipitation_probability_percent": mean([nested(p, "probabilityOfPrecipitation") for p in entries]),
            "wind_speed": mean([wind_mph(p.get("windSpeed")) for p in entries]),
            "visibility": None,  # Not provided by standard NWS hourly periods.
            "uv_index": None,  # Requires a verified supplemental source.
            "snow_probability_percent": None,  # Cannot infer snow from total precipitation.
        }
    return {"date": day.isoformat(), "timezone": timezone_name, "periods": result}
