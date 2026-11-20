from weather_supplemental import aggregate_supplemental


def sample_data():
    times = [
        f"2026-11-19T{hour:02d}:00"
        for hour in range(24)
    ]

    return {
        "timezone": "America/Chicago",
        "hourly_units": {
            "time": "iso8601",
            "visibility": "m",
            "uv_index": "",
        },
        "hourly": {
            "time": times,
            "visibility": [1609.344] * 24,
            "uv_index": [0] * 24,
        },
    }


def test_visibility_conversion():
    result = aggregate_supplemental(
        sample_data(), "2026-11-19", "America/Chicago"
    )
    assert result["morning"]["visibility_miles"] == 1.0
    assert result["night"]["visibility_miles"] == 1.0


def test_maximum_uv():
    data = sample_data()
    data["hourly"]["uv_index"][10] = 5.4

    result = aggregate_supplemental(
        data, "2026-11-19", "America/Chicago"
    )

    assert result["morning"]["uv_index"] == 5.4
    assert result["night"]["uv_index"] == 0.0


def test_missing_hour():
    data = sample_data()
    data["hourly"]["visibility"][8] = None

    result = aggregate_supplemental(
        data, "2026-11-19", "America/Chicago"
    )

    assert result["morning"]["visibility_miles"] is None
    assert result["night"]["visibility_miles"] == 1.0


def test_wrong_units():
    data = sample_data()
    data["hourly_units"]["visibility"] = "km"

    result = aggregate_supplemental(
        data, "2026-11-19", "America/Chicago"
    )

    assert result["morning"]["visibility_miles"] is None
