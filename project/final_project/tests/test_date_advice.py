"""Offline tests for date-specific Advisor helpers."""

import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "advisor-app"
    / "date_advice.py"
)

spec = importlib.util.spec_from_file_location(
    "date_advice_under_test", MODULE_PATH
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_valid_date():
    assert module.validate_date("2026-11-20")


def test_invalid_date():
    assert not module.validate_date("2026-02-30")
    assert not module.validate_date("tomorrow")


def test_fallback_cold_snow():
    result = module.fallback_period_advice({
        "temperature_f": 25,
        "snow_category": "Heavy",
        "rain_category": "No",
        "wind_speed_mph": 25,
        "visibility_miles": 0.5,
    })

    assert "coat" in result["clothing_advice"]
    assert "slippery" in result["road_advice"]
    assert "wind-resistant" in result["accessories_advice"]


def test_fallback_warm_weather():
    result = module.fallback_period_advice({
        "temperature_f": 85,
        "snow_category": "No",
        "rain_category": "No",
        "wind_speed_mph": 5,
        "visibility_miles": 10,
        "uv_index": 7,
    })

    assert "breathable" in result["clothing_advice"]
    assert "sunscreen" in result["accessories_advice"]


def test_prompt_excludes_personal_information():
    weather = {
        "city": "Chicago",
        "state": "Illinois",
        "date": "2026-11-20",
        "periods": {
            "morning": {
                "temperature_f": 45,
                "wind_speed_mph": 10,
                "relative_humidity_percent": 70,
                "rain_category": "Mild",
                "snow_category": "No",
                "visibility_miles": 5,
                "uv_index": 2,
            }
        },
    }

    prompt = module.build_period_prompt(weather, "morning")

    assert "Chicago" in prompt
    assert "Temperature F: 45" in prompt
    assert "email" not in prompt.lower()
    assert "gender" not in prompt.lower()


def test_parse_structured_advice():
    import json

    payload = {
        "road_advice": "Drive carefully.",
        "clothing_advice": "Wear a warm jacket.",
        "accessories_advice": "Bring an umbrella.",
    }

    result = module.parse_structured_advice(
        json.dumps(payload)
    )

    assert result == payload


def test_parse_structured_advice_rejects_missing_fields():
    import pytest

    with pytest.raises(ValueError):
        module.parse_structured_advice(
            '{"road_advice": "Drive carefully."}'
        )


def test_parse_structured_advice_accepts_json_code_fence():
    text = """```json
{
  "road_advice": "Watch for ice.",
  "clothing_advice": "Wear a coat.",
  "accessories_advice": "Bring gloves."
}
```"""

    result = module.parse_structured_advice(text)

    assert result["road_advice"] == "Watch for ice."
