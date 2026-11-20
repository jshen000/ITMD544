"""Offline tests for Advisor quota enforcement."""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

APP_DIR = Path(__file__).resolve().parents[1] / "advisor-app"
sys.path.insert(0, str(APP_DIR))

spec = importlib.util.spec_from_file_location(
    "advisor_quota_test", APP_DIR / "app.py"
)
advisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(advisor)


def setup_function():
    advisor.CONFIG = {
        "REPORTS_TABLE_NAME": "test-reports",
        "GEMINI_DAILY_LIMIT": 2,
    }
    advisor.DYNAMODB = MagicMock()


def test_quota_allows_request():
    with patch(
        "gemini_quota.reserve_request",
        return_value={"allowed": True, "count": 1},
    ) as reserve:
        advisor.enforce_gemini_quota()

    reserve.assert_called_once_with(
        advisor.DYNAMODB,
        "test-reports",
        daily_limit=2,
    )


def test_quota_denies_request():
    with patch(
        "gemini_quota.reserve_request",
        return_value={"allowed": False, "count": None},
    ):
        with pytest.raises(RuntimeError, match="quota"):
            advisor.enforce_gemini_quota()


def test_quota_fails_closed():
    with patch(
        "gemini_quota.reserve_request",
        side_effect=RuntimeError("DynamoDB unavailable"),
    ):
        with pytest.raises(RuntimeError):
            advisor.enforce_gemini_quota()
