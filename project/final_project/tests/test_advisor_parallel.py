"""Offline parallel Gemini processing test."""

import importlib.util
import sys
import time
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "advisor-app"
sys.path.insert(0, str(APP_DIR))

spec = importlib.util.spec_from_file_location(
    "advisor_parallel_test", APP_DIR / "app.py"
)
advisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(advisor)


WEATHER = {
    "zip": "60616",
    "city": "Chicago",
    "state": "Illinois",
    "date": "2026-11-20",
    "timezone": "America/Chicago",
    "periods": {
        "morning": {"temperature_f": 45},
        "night": {"temperature_f": 35},
    },
}


def test_parallel_period_processing():
    advisor.CONFIG = {
        "WEATHER_URL": "http://weather.example:8080",
    }

    def slow_gemini(weather, period_name):
        time.sleep(0.2)
        return {
            "road_advice": "Drive carefully.",
            "clothing_advice": "Wear a jacket.",
            "accessories_advice": "Bring essentials.",
        }

    with patch.object(
        advisor, "fetch_json", return_value=WEATHER
    ), patch.object(
        advisor, "ask_structured_gemini",
        side_effect=slow_gemini,
    ), patch.object(
        advisor, "save_report"
    ):
        started = time.monotonic()
        status, body = advisor.get_date_advice(
            "60616", "2026-11-20"
        )
        elapsed = time.monotonic() - started

    assert status == 200
    assert body["periods"]["morning"]["source"] == "gemini"
    assert body["periods"]["night"]["source"] == "gemini"

    # Two 0.2-second requests should overlap.
    assert elapsed < 0.35
