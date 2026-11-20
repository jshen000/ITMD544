from weather_periods import wind_mph, aggregate_hourly


def test_wind_speed_conversion():
    assert wind_mph("15 mph") == 15.0
    assert wind_mph("10 to 20 mph") == 15.0
    assert wind_mph("16 km/h") == 9.9
    assert wind_mph("unknown") is None
    assert wind_mph(None) is None


def test_morning_wind_aggregation():
    periods = [
        {
            "startTime": "2026-11-19T07:00:00-06:00",
            "temperature": 50,
            "windSpeed": "10 mph",
        },
        {
            "startTime": "2026-11-19T08:00:00-06:00",
            "temperature": 52,
            "windSpeed": "20 mph",
        },
    ]

    result = aggregate_hourly(
        periods, "2026-11-19", "America/Chicago"
    )

    morning = result["periods"]["morning"]

    assert morning["wind_speed"] == 15.0
    assert morning["temperature_f"] == 51.0
    assert morning["hours_available"] == 2
