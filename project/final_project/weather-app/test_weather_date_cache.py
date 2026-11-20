import importlib.util
import json
import time
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "weather_app",
    Path(__file__).with_name("app.py"),
)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class FakeDynamoDB:
    def __init__(self):
        self.items = {}

    def get_item(self, TableName, Key):
        key = Key["pk"]["S"]
        if key in self.items:
            return {"Item": self.items[key]}
        return {}

    def put_item(self, TableName, Item):
        self.items[Item["pk"]["S"]] = Item


def test_cache_write_and_read():
    app.CONFIG = {"FACTS_TABLE_NAME": "test-facts"}
    db = FakeDynamoDB()

    result = {
        "zip": "60616",
        "date": "2026-11-19",
        "periods": {"morning": {}, "night": {}},
        "cache": "miss",
    }

    assert app.write_date_cache(
        "60616", "2026-11-19", result, db
    )

    cached = app.read_date_cache(
        "60616", "2026-11-19", db
    )

    assert cached["cache"] == "hit"
    assert cached["periods"] == result["periods"]


def test_separate_date_keys():
    app.CONFIG = {"FACTS_TABLE_NAME": "test-facts"}
    db = FakeDynamoDB()

    app.write_date_cache(
        "60616", "2026-11-19", {"value": 1}, db
    )
    app.write_date_cache(
        "60616", "2026-11-20", {"value": 2}, db
    )

    assert len(db.items) == 2
    assert "weather#60616#2026-11-19" in db.items
    assert "weather#60616#2026-11-20" in db.items


def test_expired_cache_is_ignored():
    app.CONFIG = {"FACTS_TABLE_NAME": "test-facts"}
    db = FakeDynamoDB()

    key = "weather#60616#2026-11-19"

    db.items[key] = {
        "pk": {"S": key},
        "payload": {"S": json.dumps({"value": 1})},
        "expires": {"N": str(int(time.time()) - 10)},
    }

    assert app.read_date_cache(
        "60616", "2026-11-19", db
    ) is None


def test_cache_unavailable():
    assert app.read_date_cache(
        "60616", "2026-11-19", None
    ) is None



def test_cache_hit_skips_external_apis():
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    app.CONFIG = {"FACTS_TABLE_NAME": "test-facts"}
    db = FakeDynamoDB()

    selected_date = (
        datetime.now(ZoneInfo("America/Chicago")).date()
        + timedelta(days=1)
    ).isoformat()

    result = {
        "zip": "60616",
        "date": selected_date,
        "timezone": "America/Chicago",
        "periods": {"morning": {}, "night": {}},
        "cache": "miss",
    }

    app.write_date_cache(
        "60616", selected_date, result, db
    )

    with patch.object(
        app,
        "fetch_json",
        side_effect=AssertionError("Unexpected API call"),
    ):
        status, cached = app.weather_lookup_by_date(
            "60616", selected_date, db
        )

    assert status == 200
    assert cached["cache"] == "hit"


def test_invalid_cached_date_is_rejected():
    app.CONFIG = {"FACTS_TABLE_NAME": "test-facts"}
    db = FakeDynamoDB()

    selected_date = "2099-01-01"

    result = {
        "zip": "60616",
        "date": selected_date,
        "timezone": "America/Chicago",
        "periods": {"morning": {}, "night": {}},
        "cache": "miss",
    }

    app.write_date_cache(
        "60616", selected_date, result, db
    )

    with patch.object(
        app,
        "fetch_json",
        side_effect=AssertionError("Unexpected API call"),
    ):
        status, response = app.weather_lookup_by_date(
            "60616", selected_date, db
        )

    assert status == 400
    assert response == {"error": "date_out_of_range"}
