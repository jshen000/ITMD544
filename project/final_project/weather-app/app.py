"""Weather API using NWS forecasts and DynamoDB Facts."""

import json
import os
import re
import time
import logging
from datetime import datetime, timezone, date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo
from weather_periods import aggregate_hourly
from weather_grid import aggregate_grid
from weather_supplemental import aggregate_supplemental

import boto3

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("weather")

ZIP_API = os.environ.get(
    "ZIP_API_BASE", "https://api.zippopotam.us"
)
NWS_API = os.environ.get(
    "NWS_API_BASE", "https://api.weather.gov"
)
USER_AGENT = os.environ.get(
    "NWS_USER_AGENT",
    "(itmo-444-544 weather advisor, student@example.com)"
)

CACHE_SECONDS = 600


def fetch_json(url, headers=None):
    request = Request(
        url,
        headers=headers or {"User-Agent": USER_AGENT},
    )
    try:
        with urlopen(request, timeout=8) as response:
            return json.load(response)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def load_config():
    path = Path(
        os.environ.get(
            "APP_CONFIG",
            str(Path(__file__).with_name("config.json")),
        )
    )
    return json.loads(path.read_text())


def weather_lookup(zip_code, table):
    now = int(time.time())
    key = {"pk": {"S": f"weather#{zip_code}"}}

    if table is not None:
        try:
            cached = table.get_item(
                TableName=CONFIG["FACTS_TABLE_NAME"],
                Key=key,
            ).get("Item")

            if cached and int(cached["expires"]["N"]) > now:
                result = json.loads(cached["payload"]["S"])
                result["cache"] = "hit"
                return 200, result
        except Exception:
            log.exception("DynamoDB cache read failed")

    location = fetch_json(f"{ZIP_API}/us/{zip_code}")

    if not location or not location.get("places"):
        return 404, {"error": "zip_not_found"}

    place = location["places"][0]
    lat = float(place["latitude"])
    lon = float(place["longitude"])

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/geo+json",
    }

    point = fetch_json(
        f"{NWS_API}/points/{lat:.4f},{lon:.4f}",
        headers,
    )

    if not point:
        return 404, {"error": "no_nws_coverage"}

    forecast_url = point["properties"]["forecast"]
    forecast = fetch_json(forecast_url, headers)

    if not forecast:
        return 502, {"error": "forecast_unavailable"}

    period = forecast["properties"]["periods"][0]

    precipitation = period.get(
        "probabilityOfPrecipitation"
    ) or {}

    result = {
        "zip": zip_code,
        "city": place["place name"],
        "state": place["state abbreviation"],
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "forecast": {
            "name": period["name"],
            "temperature": period["temperature"],
            "temperatureUnit": period["temperatureUnit"],
            "shortForecast": period["shortForecast"],
            "windSpeed": period["windSpeed"],
            "precipitationChance": precipitation.get("value"),
            "isDaytime": period["isDaytime"],
        },
        "cache": "miss",
    }

    if table is not None:
        try:
            table.put_item(
                TableName=CONFIG["FACTS_TABLE_NAME"],
                Item={
                    **key,
                    "payload": {"S": json.dumps(result)},
                    "expires": {
                        "N": str(now + CACHE_SECONDS)
                    },
                },
            )
        except Exception:
            log.exception("DynamoDB cache write failed")
            result["cache"] = "unavailable"

    return 200, result




def read_date_cache(zip_code, selected_date, table):
    """Read an unexpired date-specific forecast from DynamoDB."""
    if table is None:
        return None

    now = int(time.time())
    key = {
        "pk": {"S": f"weather#{zip_code}#{selected_date}"}
    }

    try:
        item = table.get_item(
            TableName=CONFIG["FACTS_TABLE_NAME"],
            Key=key,
        ).get("Item")

        if item and int(item["expires"]["N"]) > now:
            result = json.loads(item["payload"]["S"])
            result["cache"] = "hit"
            return result

    except Exception:
        log.exception("Date cache read failed")

    return None


def write_date_cache(zip_code, selected_date, result, table):
    """Store a date-specific forecast with a DynamoDB TTL."""
    if table is None:
        return False

    now = int(time.time())

    try:
        table.put_item(
            TableName=CONFIG["FACTS_TABLE_NAME"],
            Item={
                "pk": {
                    "S": f"weather#{zip_code}#{selected_date}"
                },
                "payload": {
                    "S": json.dumps(result)
                },
                "expires": {
                    "N": str(now + CACHE_SECONDS)
                },
            },
        )
        return True

    except Exception:
        log.exception("Date cache write failed")
        return False


def weather_lookup_by_date(zip_code, selected_date, table=None):
    """Fetch hourly NWS data and summarize local morning/night periods."""

    cached = read_date_cache(zip_code, selected_date, table)
    if cached is not None:
        timezone_name = cached.get("timezone")
        if timezone_name:
            try:
                local_today = datetime.now(
                    ZoneInfo(timezone_name)
                ).date()
                requested = date.fromisoformat(selected_date)

                if (
                    local_today <= requested
                    < local_today + timedelta(days=7)
                ):
                    return 200, cached

                return 400, {"error": "date_out_of_range"}

            except (ValueError, KeyError):
                log.warning("Invalid cached forecast timezone")

        # Missing/invalid timezone: fetch a fresh forecast.


    location = fetch_json(f"{ZIP_API}/us/{zip_code}")
    if not location or not location.get("places"):
        return 404, {"error": "zip_not_found"}

    place = location["places"][0]
    lat = float(place["latitude"])
    lon = float(place["longitude"])

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/geo+json",
    }

    point = fetch_json(
        f"{NWS_API}/points/{lat:.4f},{lon:.4f}",
        headers,
    )
    if not point:
        return 404, {"error": "no_nws_coverage"}

    properties = point.get("properties", {})
    timezone_name = properties.get("timeZone")
    hourly_url = properties.get("forecastHourly")

    if not timezone_name or not hourly_url:
        return 502, {"error": "hourly_forecast_unavailable"}

    local_today = datetime.now(ZoneInfo(timezone_name)).date()
    requested = date.fromisoformat(selected_date)

    if not local_today <= requested < local_today + timedelta(days=7):
        return 400, {"error": "date_out_of_range"}

    forecast = fetch_json(hourly_url, headers)
    if not forecast:
        return 502, {"error": "hourly_forecast_unavailable"}

    periods = forecast.get("properties", {}).get("periods", [])
    summary = aggregate_hourly(periods, selected_date, timezone_name)

    # Gridded NWS forecast supplies quantitative precipitation
    # and snowfall amounts. Missing data remains unavailable.
    grid_properties = {}
    grid_url = properties.get("forecastGridData")

    if grid_url:
        try:
            grid = fetch_json(grid_url, headers)
            if isinstance(grid, dict):
                grid_properties = grid.get("properties", {})
        except (HTTPError, URLError, TimeoutError, ValueError):
            log.warning("NWS grid forecast unavailable")

    grid_summary = aggregate_grid(
        grid_properties, selected_date, timezone_name
    )

    for period_name in ("morning", "night"):
        summary["periods"][period_name].update(
            grid_summary[period_name]
        )

    # Open-Meteo provides forecast visibility and UV index.
    # Keep missing fields as None when the API is unavailable.
    from urllib.parse import urlencode

    supplemental_data = None

    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "visibility,uv_index",
        "forecast_days": 8,
        "timezone": timezone_name,
    }

    supplemental_url = (
        "https://api.open-meteo.com/v1/forecast?"
        + urlencode(params)
    )

    try:
        supplemental_data = fetch_json(supplemental_url)
    except Exception:
        log.warning("Supplemental weather forecast unavailable")

    supplemental = aggregate_supplemental(
        supplemental_data, selected_date, timezone_name
    )

    for period_name in ("morning", "night"):
        summary["periods"][period_name].update(
            supplemental[period_name]
        )

    result = {
        "zip": zip_code,
        "city": place.get("place name"),
        "state": place.get("state abbreviation"),
        "date": selected_date,
        "timezone": timezone_name,
        "periods": summary["periods"],
        "source": "NWS forecastHourly",
        "cache": "miss",
    }

    if table is not None:
        if not write_date_cache(
            zip_code, selected_date, result, table
        ):
            result["cache"] = "unavailable"

    return 200, result


class WeatherHandler(BaseHTTPRequestHandler):
    def send_json(self, status, body):
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlsplit(self.path)

        if parsed.path in ("/", "/health"):
            return self.send_json(
                200,
                {"status": "ok", "service": "weather"},
            )

        if parsed.path != "/weather":
            return self.send_json(
                404, {"error": "not_found"}
            )

        zip_code = parse_qs(parsed.query).get("zip", [""])[0]

        if not re.fullmatch(r"[0-9]{5}", zip_code):
            return self.send_json(
                400, {"error": "invalid_zip"}
            )

        selected_date = parse_qs(parsed.query).get("date", [None])[0]

        if selected_date is not None:
            try:
                date.fromisoformat(selected_date)
                if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", selected_date):
                    raise ValueError("Invalid date format")
            except ValueError:
                return self.send_json(
                    400, {"error": "invalid_date"}
                )

        try:
            if selected_date is not None:
                status, result = weather_lookup_by_date(zip_code, selected_date, DYNAMODB)
            else:
                status, result = weather_lookup(
                    zip_code, DYNAMODB
                )
        except (HTTPError, URLError, TimeoutError):
            log.exception("External weather API failed")
            status, result = 502, {"error": "upstream_error"}
        except Exception:
            log.exception("Weather request failed")
            status, result = 500, {"error": "internal_error"}

        self.send_json(status, result)


def main():
    global CONFIG, DYNAMODB

    CONFIG = load_config()
    DYNAMODB = boto3.client(
        "dynamodb",
        region_name=CONFIG["AWS_REGION"],
    )

    port = int(CONFIG["PORT"])
    server = ThreadingHTTPServer(
        ("0.0.0.0", port),
        WeatherHandler,
    )

    log.info("Weather API started on port %s", port)
    server.serve_forever()


if __name__ == "__main__":
    main()
