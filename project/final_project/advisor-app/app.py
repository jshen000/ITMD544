"""Weather Advisor: Python HTTP service using AWS and Gemini."""

import json
import os
import re
import time
import logging
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import boto3


log = logging.getLogger("advisor")
CONFIG = {}
SSM = None
DYNAMODB = None
KEY_CACHE = {"value": None, "expires": 0}

MODEL = "gemini-3.1-flash-lite"
GEMINI_BASE = "https://generativelanguage.googleapis.com"

SYSTEM_PROMPT = (
    "Give a short clothing suggestion for the supplied weather. "
    "Use at most two plain sentences, without markdown or lists. "
    "Use only the weather information provided."
)


def load_config():
    path = Path(os.environ.get(
        "APP_CONFIG",
        str(Path(__file__).with_name("config.json"))
    ))
    return json.loads(path.read_text())


def fetch_json(url, *, data=None, headers=None, timeout=15):
    request = Request(
        url,
        data=data,
        headers=headers or {},
        method="POST" if data is not None else "GET",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def get_api_key():
    local_key = os.environ.get("GEMINI_API_KEY")
    if local_key:
        return local_key

    now = time.time()
    if KEY_CACHE["value"] and now < KEY_CACHE["expires"]:
        return KEY_CACHE["value"]

    response = SSM.get_parameter(
        Name=CONFIG["GEMINI_PARAM_NAME"],
        WithDecryption=True,
    )
    key = response["Parameter"]["Value"]
    KEY_CACHE.update(value=key, expires=now + 300)
    return key


def build_prompt(weather):
    forecast = weather["forecast"]
    return "\n".join([
        f"Zip code: {weather['zip']} "
        f"({weather['city']}, {weather['state']})",
        f"Period: {forecast['name']}",
        f"Temperature: {forecast['temperature']} "
        f"{forecast['temperatureUnit']}",
        f"Conditions: {forecast['shortForecast']}",
        f"Wind: {forecast['windSpeed']}",
        "Chance of precipitation: "
        f"{forecast.get('precipitationChance')}",
    ])




def enforce_gemini_quota():
    """Reserve Gemini quota before sending an API request."""
    from gemini_quota import reserve_request

    limit = int(CONFIG.get("GEMINI_DAILY_LIMIT", 100))

    result = reserve_request(
        DYNAMODB,
        CONFIG["REPORTS_TABLE_NAME"],
        daily_limit=limit,
    )

    if not result["allowed"]:
        raise RuntimeError("gemini_daily_quota_exceeded")


def ask_gemini(weather):
    enforce_gemini_quota()
    key = get_api_key()

    url = (
        f"{GEMINI_BASE}/v1beta/models/"
        f"{MODEL}:generateContent"
    )
    payload = {
        "systemInstruction": {
            "parts": [{"text": SYSTEM_PROMPT}]
        },
        "contents": [{
            "parts": [{"text": build_prompt(weather)}]
        }],
        "generationConfig": {"maxOutputTokens": 300},
    }

    response = fetch_json(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": key,
        },
        timeout=15,
    )

    parts = (
        response.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [])
    )
    advice = "".join(
        part.get("text", "") for part in parts
    ).strip()

    if not advice:
        raise ValueError("gemini_empty_response")

    return advice


def fallback_advice(forecast):
    temperature = float(forecast["temperature"])

    if forecast.get("temperatureUnit") == "C":
        temperature = temperature * 9 / 5 + 32

    if temperature <= 32:
        advice = "Wear a heavy coat, a hat and gloves."
    elif temperature <= 50:
        advice = "Wear a warm jacket and long pants."
    elif temperature <= 68:
        advice = "Wear a light jacket or a sweater."
    elif temperature <= 80:
        advice = "A t-shirt and light pants will be fine."
    else:
        advice = "Wear breathable clothes and bring water."

    rain = forecast.get("precipitationChance") or 0
    conditions = forecast.get("shortForecast", "")

    if rain >= 40 or re.search(
        r"rain|shower|storm|snow", conditions, re.I
    ):
        advice += " Bring an umbrella or waterproof jacket."

    return advice


def save_report(zip_code, weather, advice, source):
    now = int(time.time())
    report = {
        "zip": zip_code,
        "weather": weather,
        "advice": advice,
        "source": source,
        "model": MODEL if source == "gemini" else None,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }

    DYNAMODB.put_item(
        TableName=CONFIG["REPORTS_TABLE_NAME"],
        Item={
            "pk": {"S": f"report#{zip_code}#{time.time_ns()}"},
            "payload": {"S": json.dumps(report)},
            "expires": {"N": str(now + 86400)},
        },
    )


def get_advice(zip_code):
    if not re.fullmatch(r"[0-9]{5}", zip_code):
        return 400, {
            "error": "invalid_zip",
            "message": "Give a 5 digit US zip code.",
        }

    weather_url = CONFIG["WEATHER_URL"].rstrip("/")
    url = f"{weather_url}/weather?zip={quote(zip_code)}"

    try:
        weather = fetch_json(url, timeout=20)
    except HTTPError as error:
        if error.code in (400, 404):
            return error.code, {
                "error": "weather_not_found"
            }
        log.warning("Weather returned HTTP %s", error.code)
        return 502, {"error": "weather_service_unavailable"}
    except Exception:
        log.exception("Weather request failed")
        return 502, {"error": "weather_service_unavailable"}

    try:
        advice = ask_gemini(weather)
        source = "gemini"
    except Exception as error:
        # Never log the API key or raw Gemini response.
        log.warning("Gemini unavailable: %s", type(error).__name__)
        advice = fallback_advice(weather["forecast"])
        source = "fallback"

    try:
        save_report(zip_code, weather, advice, source)
    except Exception:
        log.exception("Unable to save DynamoDB report")

    return 200, {
        "zip": zip_code,
        "weather": weather,
        "advice": advice,
        "source": source,
        "model": MODEL if source == "gemini" else None,
    }






def ask_structured_gemini(weather, period_name):
    """Request validated road, clothing and accessory advice."""
    from date_advice import (
        build_period_prompt,
        parse_structured_advice,
    )

    enforce_gemini_quota()
    key = get_api_key()
    prompt = build_period_prompt(weather, period_name)

    payload = {
        "systemInstruction": {
            "parts": [{
                "text": (
                    "Return only a JSON object with exactly three "
                    "nonempty string fields: road_advice, "
                    "clothing_advice, accessories_advice. "
                    "Use only the supplied weather measurements. "
                    "Do not invent missing measurements."
                )
            }]
        },
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "maxOutputTokens": 400,
        },
    }

    url = (
        f"{GEMINI_BASE}/v1beta/models/"
        f"{MODEL}:generateContent"
    )

    response = fetch_json(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": key,
        },
        timeout=15,
    )

    parts = (
        response.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [])
    )

    text = "".join(
        part.get("text", "") for part in parts
    )

    return parse_structured_advice(text)


def get_date_advice(zip_code, selected_date):
    """Generate a two-period report for a selected date."""
    from date_advice import (
        validate_date,
        build_period_prompt,
        fallback_period_advice,
    )

    if not re.fullmatch(r"[0-9]{5}", zip_code):
        return 400, {"error": "invalid_zip"}

    if not validate_date(selected_date):
        return 400, {"error": "invalid_date"}

    weather_url = CONFIG["WEATHER_URL"].rstrip("/")
    url = (
        f"{weather_url}/weather?zip={quote(zip_code)}"
        f"&date={quote(selected_date)}"
    )

    try:
        weather = fetch_json(url, timeout=20)
    except HTTPError as error:
        if error.code in (400, 404):
            return error.code, {"error": "weather_not_found"}
        return 502, {"error": "weather_service_unavailable"}
    except Exception:
        log.exception("Date-specific weather request failed")
        return 502, {"error": "weather_service_unavailable"}

    if not isinstance(weather, dict):
        return 502, {"error": "invalid_weather_response"}

    periods = weather.get("periods")
    if not isinstance(periods, dict):
        return 502, {"error": "invalid_weather_response"}

    from date_advice import fallback_period_advice

    from concurrent.futures import ThreadPoolExecutor

    def process_period(period_name):
        period = periods.get(period_name)

        if not isinstance(period, dict):
            return None

        try:
            advice = ask_structured_gemini(
                weather, period_name
            )
            return {
                "source": "gemini",
                "advice": advice,
            }
        except Exception as error:
            log.warning(
                "Period advice fallback: %s",
                type(error).__name__,
            )
            return {
                "source": "fallback",
                "advice": fallback_period_advice(period),
            }

    with ThreadPoolExecutor(max_workers=2) as executor:
        morning_future = executor.submit(
            process_period, "morning"
        )
        night_future = executor.submit(
            process_period, "night"
        )

        results = {
            "morning": morning_future.result(),
            "night": night_future.result(),
        }

    if any(value is None for value in results.values()):
        return 502, {"error": "missing_weather_period"}

    report = {
        "zip": zip_code,
        "date": selected_date,
        "city": weather.get("city"),
        "state": weather.get("state"),
        "timezone": weather.get("timezone"),
        "weather": weather,
        "periods": results,
    }

    try:
        save_report(zip_code, weather, results, "period_report")
    except Exception:
        log.exception("Unable to save date-specific report")

    return 200, report


class AdvisorHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def respond(self, status, body):
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        parsed = urlsplit(self.path)

        if parsed.path in ("/", "/health"):
            self.respond(200, {
                "status": "ok",
                "service": "advisor",
            })
        elif parsed.path == "/advice":
            zip_code = parse_qs(
                parsed.query
            ).get("zip", [""])[0]
            selected_date = parse_qs(
                parsed.query
            ).get("date", [None])[0]

            if selected_date is None:
                status, body = get_advice(zip_code)
            else:
                status, body = get_date_advice(
                    zip_code, selected_date
                )

            self.respond(status, body)
        else:
            self.respond(404, {"error": "not_found"})


def main():
    global CONFIG, SSM, DYNAMODB

    logging.basicConfig(level=logging.INFO)
    CONFIG = load_config()

    region = CONFIG["AWS_REGION"]
    SSM = boto3.client("ssm", region_name=region)
    DYNAMODB = boto3.client("dynamodb", region_name=region)

    port = int(CONFIG["PORT"])
    if not 1 <= port <= 65535:
        raise ValueError("Invalid PORT")

    server = ThreadingHTTPServer(
        ("0.0.0.0", port), AdvisorHandler
    )
    server.daemon_threads = True

    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
