"""Live HTTP integration tests for Weather and Advisor.

Run only after deploying the AWS stack:
    pytest -m integration
"""

import pytest
import requests

from tests.helpers import tier_url

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def weather_url(settings, outputs):
    return tier_url(outputs, settings, "weather")


@pytest.fixture(scope="module")
def advisor_url(settings, outputs):
    return tier_url(outputs, settings, "advisor")


def test_weather_health(weather_url):
    response = requests.get(weather_url + "health", timeout=10)

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "weather",
    }


def test_advisor_health(advisor_url):
    response = requests.get(advisor_url + "health", timeout=10)

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "advisor",
    }


def test_weather_rejects_invalid_zip(weather_url):
    response = requests.get(
        weather_url + "weather",
        params={"zip": "abc"},
        timeout=10,
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_zip"


def test_weather_rejects_invalid_date(weather_url):
    response = requests.get(
        weather_url + "weather",
        params={"zip": "60616", "date": "bad"},
        timeout=10,
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_date"


def test_unknown_route_returns_404(weather_url, advisor_url):
    for url in (weather_url, advisor_url):
        response = requests.get(
            url + "nonexistent",
            timeout=10,
        )

        assert response.status_code == 404
        assert response.json()["error"] == "not_found"
