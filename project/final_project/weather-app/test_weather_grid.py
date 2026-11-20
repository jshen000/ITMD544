from datetime import datetime, timezone
from weather_grid import (
    parse_duration,
    average_rate,
    aggregate_grid,
)


def test_nws_durations():
    assert parse_duration("PT3H").total_seconds() == 10800
    assert parse_duration("PT6H").total_seconds() == 21600
    assert parse_duration("P1DT3H").total_seconds() == 97200


def test_grid_rate():
    field = {
        "uom": "wmoUnit:mm",
        "values": [{
            "validTime": "2026-11-19T12:00:00+00:00/PT6H",
            "value": 12,
        }],
    }
    start = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
    end = datetime(2026, 11, 19, 18, tzinfo=timezone.utc)
    assert average_rate(field, start, end, "wmoUnit:mm") == 2.0


def test_missing_coverage():
    result = aggregate_grid(
        {}, "2026-11-19", "America/Chicago"
    )
    assert result["morning"]["rain_category"] == "Not available"
    assert result["night"]["snow_category"] == "Not available"


def test_zero_precipitation():
    field = {
        "uom": "wmoUnit:mm",
        "values": [{
            "validTime": "2026-11-19T12:00:00+00:00/PT6H",
            "value": 0,
        }],
    }
    result = aggregate_grid(
        {
            "quantitativePrecipitation": field,
            "snowfallAmount": field,
        },
        "2026-11-19",
        "America/Chicago",
    )
    assert result["morning"]["rain_category"] == "No"
    assert result["morning"]["snow_category"] == "No"
