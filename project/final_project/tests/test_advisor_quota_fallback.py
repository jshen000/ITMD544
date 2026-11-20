"""Verify quota denial triggers fallback without Gemini calls."""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1] / "advisor-app"
sys.path.insert(0, str(APP_DIR))

spec = importlib.util.spec_from_file_location(
    "advisor_quota_fallback_test", APP_DIR / "app.py"
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
        "morning": {
            "temperature_f": 40,
            "rain_category": "Mild",
        },
        "night": {
            "temperature_f": 30,
            "snow_category": "Heavy",
        },
    },
}


def test_quota_denial_falls_back_for_both_periods():
    advisor.CONFIG = {
        "WEATHER_URL": "http://weather.example:8080",
    }

    with patch.object(
        advisor, "fetch_json", return_value=WEATHER
    ), patch.object(
        advisor, "enforce_gemini_quota",
        side_effect=RuntimeError("gemini_daily_quota_exceeded"),
    ) as quota, patch.object(
        advisor, "get_api_key"
    ) as api_key, patch.object(
        advisor, "save_report"
    ):
        status, body = advisor.get_date_advice(
            "60616", "2026-11-20"
        )

    assert status == 200
    assert quota.call_count == 2
    api_key.assert_not_called()

    for period in ("morning", "night"):
        result = body["periods"][period]
        assert result["source"] == "fallback"
        assert result["advice"]["road_advice"]
        assert result["advice"]["clothing_advice"]
        assert result["advice"]["accessories_advice"]
