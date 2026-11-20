from precipitation import rain_category, snow_category


def test_rain_categories():
    assert rain_category(0) == "No"
    assert rain_category(1.5) == "Mild"
    assert rain_category(7.6) == "Heavy"
    assert rain_category(12) == "Heavy"


def test_snow_categories():
    assert snow_category(0) == "No"
    assert snow_category(1.0) == "Mild"
    assert snow_category(2.5) == "Heavy"


def test_missing_and_invalid_values():
    for value in (None, -1, "unknown", float("nan")):
        assert rain_category(value) == "Not available"
        assert snow_category(value) == "Not available"


def test_probability_is_not_intensity():
    # A probability percentage must never be supplied as a rate.
    assert rain_category(None) == "Not available"
    assert snow_category(None) == "Not available"
