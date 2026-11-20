"""Aggregate Open-Meteo visibility and UV forecasts."""

import math
from datetime import datetime
from zoneinfo import ZoneInfo

from weather_periods import WINDOWS

METERS_PER_MILE = 1609.344


def aggregate_supplemental(data, selected_date, timezone_name):
    result = {
        name: {
            "visibility_miles": None,
            "uv_index": None,
            "supplemental_hours_available": 0,
        }
        for name in WINDOWS
    }

    if not isinstance(data, dict):
        return result

    hourly = data.get("hourly", {})
    units = data.get("hourly_units", {})

    if not isinstance(hourly, dict) or not isinstance(units, dict):
        return result

    times = hourly.get("time", [])
    visibility = hourly.get("visibility", [])
    uv_values = hourly.get("uv_index", [])

    if not all(isinstance(x, list) for x in
               (times, visibility, uv_values)):
        return result

    if units.get("visibility") != "m":
        return result

    if units.get("uv_index") != "":
        return result

    if data.get("timezone") != timezone_name:
        return result

    buckets = {
        name: {"visibility": [], "uv": []}
        for name in WINDOWS
    }

    for time_text, vis, uv in zip(
        times, visibility, uv_values
    ):
        try:
            local = datetime.fromisoformat(time_text)
        except (TypeError, ValueError):
            continue

        if local.tzinfo is not None:
            local = local.astimezone(ZoneInfo(timezone_name))

        if local.date().isoformat() != selected_date:
            continue

        for name, hours in WINDOWS.items():
            if local.hour not in hours:
                continue

            if (
                isinstance(vis, (int, float))
                and not isinstance(vis, bool)
                and math.isfinite(vis)
                and vis >= 0
            ):
                buckets[name]["visibility"].append(
                    vis / METERS_PER_MILE
                )

            if (
                isinstance(uv, (int, float))
                and not isinstance(uv, bool)
                and math.isfinite(uv)
                and uv >= 0
            ):
                buckets[name]["uv"].append(uv)

    for name, values in buckets.items():
        vis_values = values["visibility"]
        uv_values = values["uv"]

        # Require all six hourly values for each metric.
        if len(vis_values) == 6:
            result[name]["visibility_miles"] = round(
                sum(vis_values) / 6, 1
            )

        if len(uv_values) == 6:
            result[name]["uv_index"] = round(
                max(uv_values), 1
            )

        result[name]["supplemental_hours_available"] = min(
            len(vis_values), len(uv_values)
        )

    return result
