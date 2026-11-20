from weather_periods import aggregate_hourly


def test_morning_night_and_missing_data():
    rows = [
        {"startTime": "2026-11-18T07:00:00-06:00", "temperature": 50,
         "relativeHumidity": {"value": 70}, "probabilityOfPrecipitation": {"value": 20}},
        {"startTime": "2026-11-18T08:00:00-06:00", "temperature": 54,
         "relativeHumidity": {"value": None}, "probabilityOfPrecipitation": {"value": 40}},
        {"startTime": "2026-11-18T20:00:00-06:00", "temperature": 40},
        {"startTime": "2026-11-19T07:00:00-06:00", "temperature": 99},
    ]
    data = aggregate_hourly(rows, "2026-11-18", "America/Chicago")
    assert data["periods"]["morning"]["temperature_f"] == 52
    assert data["periods"]["morning"]["relative_humidity_percent"] == 70
    assert data["periods"]["morning"]["precipitation_probability_percent"] == 30
    assert data["periods"]["night"]["temperature_f"] == 40
    assert data["periods"]["night"]["uv_index"] is None


def test_dst_timezone_conversion():
    rows = [{"startTime": "2026-07-01T11:00:00Z", "temperature": 70}]
    data = aggregate_hourly(rows, "2026-07-01", "America/Chicago")
    assert data["periods"]["morning"]["hours_available"] == 1
