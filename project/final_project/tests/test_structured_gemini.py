"""Offline tests for structured Gemini integration."""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "advisor-app"

sys.path.insert(0, str(APP_DIR))

spec = importlib.util.spec_from_file_location(
    "advisor_structured_test", APP_DIR / "app.py"
)
advisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(advisor)


WEATHER = {
    "zip": "60616",
    "city": "Chicago",
    "state": "Illinois",
    "date": "2026-11-20",
    "periods": {
        "morning": {
            "temperature_f": 45,
            "rain_category": "Mild",
        },
        "night": {
            "temperature_f": 35,
            "rain_category": "No",
        },
    },
}




@pytest.fixture(autouse=True)
def mock_gemini_quota():
    """Keep Gemini response tests independent of DynamoDB."""
    with patch.object(advisor, "enforce_gemini_quota"):
        yield


def test_structured_gemini_success():
    expected = {
        "road_advice": "Drive carefully.",
        "clothing_advice": "Wear a jacket.",
        "accessories_advice": "Bring an umbrella.",
    }

    response = {
        "candidates": [{
            "content": {
                "parts": [{"text": json.dumps(expected)}]
            }
        }]
    }

    with patch.object(
        advisor, "get_api_key", return_value="test-key"
    ), patch.object(
        advisor, "fetch_json", return_value=response
    ) as fetch:

        result = advisor.ask_structured_gemini(
            WEATHER, "morning"
        )

    assert result == expected

    kwargs = fetch.call_args.kwargs
    payload = json.loads(kwargs["data"])

    assert (
        payload["generationConfig"]["responseMimeType"]
        == "application/json"
    )

    prompt = payload["contents"][0]["parts"][0]["text"]
    assert "Period: morning" in prompt
    assert "Chicago" in prompt


def test_structured_gemini_invalid_response():
    response = {
        "candidates": [{
            "content": {
                "parts": [{"text": '{"road_advice":"Drive carefully."}'}]
            }
        }]
    }

    with patch.object(
        advisor, "get_api_key", return_value="test-key"
    ), patch.object(
        advisor, "fetch_json", return_value=response
    ):
        with pytest.raises(ValueError):
            advisor.ask_structured_gemini(
                WEATHER, "night"
            )
