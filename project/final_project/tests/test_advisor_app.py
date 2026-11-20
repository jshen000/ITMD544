"""Offline tests for the Python Advisor application."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, patch


APP_PATH = (
    Path(__file__).resolve().parents[1]
    / "advisor-app"
    / "app.py"
)

spec = importlib.util.spec_from_file_location(
    "advisor_under_test", APP_PATH
)
advisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(advisor)


WEATHER = {
    "zip": "60616",
    "city": "Chicago",
    "state": "Illinois",
    "forecast": {
        "name": "Today",
        "temperature": 45,
        "temperatureUnit": "F",
        "shortForecast": "Light Rain",
        "windSpeed": "10 mph",
        "precipitationChance": 70,
    },
}


def setup_function():
    advisor.CONFIG = {
        "AWS_REGION": "us-east-2",
        "WEATHER_URL": "http://weather.example:8080",
        "GEMINI_PARAM_NAME": "/test/gemini-key",
        "REPORTS_TABLE_NAME": "test-reports",
    }
    advisor.SSM = MagicMock()
    advisor.DYNAMODB = MagicMock()
    advisor.KEY_CACHE = {"value": None, "expires": 0}


def test_invalid_zip():
    status, body = advisor.get_advice("invalid")

    assert status == 400
    assert body["error"] == "invalid_zip"
    advisor.DYNAMODB.put_item.assert_not_called()


def test_gemini_success():
    advisor.SSM.get_parameter.return_value = {
        "Parameter": {"Value": "fake-test-key"}
    }

    gemini_response = {
        "candidates": [{
            "content": {
                "parts": [{
                    "text": "Wear a warm jacket and carry an umbrella."
                }]
            }
        }]
    }

    with patch.object(
        advisor,
        "fetch_json",
        side_effect=[WEATHER, gemini_response],
    ) as fetch:
        with patch.dict(
            "os.environ", {"GEMINI_API_KEY": ""}, clear=False
        ):
            status, body = advisor.get_advice("60616")

    assert status == 200
    assert body["source"] == "gemini"
    assert "warm jacket" in body["advice"]

    advisor.SSM.get_parameter.assert_called_once_with(
        Name="/test/gemini-key",
        WithDecryption=True,
    )

    assert fetch.call_count == 2
    assert fetch.call_args_list[0].args[0] == (
        "http://weather.example:8080/weather?zip=60616"
    )

    advisor.DYNAMODB.put_item.assert_called_once()


def test_gemini_failure_uses_fallback():
    with patch.object(
        advisor,
        "fetch_json",
        side_effect=[WEATHER, RuntimeError("Gemini unavailable")],
    ):
        with patch.object(
            advisor, "get_api_key", return_value="fake-test-key"
        ):
            status, body = advisor.get_advice("60616")

    assert status == 200
    assert body["source"] == "fallback"
    assert "jacket" in body["advice"].lower()
    assert "umbrella" in body["advice"].lower()

    advisor.DYNAMODB.put_item.assert_called_once()


def test_dynamodb_report():
    advisor.save_report(
        "60616",
        WEATHER,
        "Wear a warm jacket.",
        "fallback",
    )

    kwargs = advisor.DYNAMODB.put_item.call_args.kwargs

    assert kwargs["TableName"] == "test-reports"

    item = kwargs["Item"]
    assert item["pk"]["S"].startswith("report#60616#")

    payload = json.loads(item["payload"]["S"])
    assert payload["zip"] == "60616"
    assert payload["source"] == "fallback"
    assert payload["advice"] == "Wear a warm jacket."
    assert int(item["expires"]["N"]) > 0


def test_weather_service_failure():
    with patch.object(
        advisor,
        "fetch_json",
        side_effect=TimeoutError("Weather timeout"),
    ):
        status, body = advisor.get_advice("60616")

    assert status == 502
    assert body["error"] == "weather_service_unavailable"
    advisor.DYNAMODB.put_item.assert_not_called()
