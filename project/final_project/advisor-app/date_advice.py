"""Date-specific Morning/Night weather advice."""

import re
from datetime import date


def validate_date(selected_date):
    if not isinstance(selected_date, str):
        return False

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", selected_date):
        return False

    try:
        date.fromisoformat(selected_date)
        return True
    except ValueError:
        return False


def build_period_prompt(weather, period_name):
    """Build a Gemini prompt without personal information."""

    period = weather["periods"][period_name]

    fields = [
        f"Location: {weather['city']}, {weather['state']}",
        f"Date: {weather['date']}",
        f"Period: {period_name}",
        f"Temperature F: {period.get('temperature_f')}",
        f"Wind mph: {period.get('wind_speed_mph')}",
        f"Humidity percent: {period.get('relative_humidity_percent')}",
        f"Rain: {period.get('rain_category')}",
        f"Snow: {period.get('snow_category')}",
        f"Visibility miles: {period.get('visibility_miles')}",
        f"UV index: {period.get('uv_index')}",
        (
            "Provide concise road, clothing, and accessories "
            "recommendations based only on the supplied weather. "
            "Do not invent missing measurements."
        ),
    ]

    return "\n".join(fields)


def fallback_period_advice(period):
    """Deterministic advice when Gemini is unavailable."""

    temperature = period.get("temperature_f")
    rain = period.get("rain_category")
    snow = period.get("snow_category")
    wind = period.get("wind_speed_mph")
    visibility = period.get("visibility_miles")

    if temperature is None:
        clothing = "Check the temperature before leaving."
    elif temperature <= 32:
        clothing = "Wear an insulated coat, hat, and gloves."
    elif temperature <= 50:
        clothing = "Wear a warm jacket and long pants."
    elif temperature <= 68:
        clothing = "Wear a light jacket or sweater."
    elif temperature <= 80:
        clothing = "Wear comfortable, lightweight clothing."
    else:
        clothing = "Wear breathable clothing and stay hydrated."

    accessories = []

    if rain in ("Mild", "Heavy"):
        accessories.append("umbrella or waterproof jacket")

    if snow in ("Mild", "Heavy"):
        accessories.append("waterproof footwear")

    if wind is not None and wind >= 20:
        accessories.append("wind-resistant outerwear")

    uv = period.get("uv_index")
    if uv is not None and uv >= 3:
        accessories.append("sunscreen and sunglasses")

    if not accessories:
        accessories.append("usual daily essentials")

    road = "Follow local road and traffic advisories."

    if snow in ("Mild", "Heavy"):
        road = "Allow extra travel time; roads may be slippery."
    elif rain == "Heavy":
        road = "Use caution in heavy rain and avoid flooded roads."
    elif visibility is not None and visibility < 1:
        road = "Visibility may be poor; reduce speed."

    return {
        "road_advice": road,
        "clothing_advice": clothing,
        "accessories_advice": "Bring " + ", ".join(accessories) + ".",
    }


def parse_structured_advice(text):
    """Validate a JSON recommendation returned by Gemini."""
    import json

    if not isinstance(text, str):
        raise ValueError("Advice must be a string")

    cleaned = text.strip()

    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            cleaned = "\n".join(lines[1:-1]).strip()

    result = json.loads(cleaned)

    required = (
        "road_advice",
        "clothing_advice",
        "accessories_advice",
    )

    if not isinstance(result, dict):
        raise ValueError("Advice must be a JSON object")

    for key in required:
        value = result.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Missing or invalid {key}")

    return {key: result[key].strip() for key in required}
