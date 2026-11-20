"""Offline Gemini quota tests."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError


path = (
    Path(__file__).resolve().parents[1]
    / "advisor-app"
    / "gemini_quota.py"
)

spec = importlib.util.spec_from_file_location("gemini_quota_test", path)
quota = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quota)


def test_quota_allows_request():
    client = MagicMock()
    client.update_item.return_value = {
        "Attributes": {"request_count": {"N": "1"}}
    }

    result = quota.reserve_request(client, "test-reports", 100)

    assert result == {"allowed": True, "count": 1}
    client.update_item.assert_called_once()


def test_quota_denies_when_limit_reached():
    client = MagicMock()
    client.update_item.side_effect = ClientError(
        {
            "Error": {
                "Code": "ConditionalCheckFailedException",
                "Message": "Quota reached",
            }
        },
        "UpdateItem",
    )

    result = quota.reserve_request(client, "test-reports", 100)

    assert result["allowed"] is False


def test_quota_rejects_invalid_limit():
    with pytest.raises(ValueError):
        quota.reserve_request(MagicMock(), "test-reports", 0)


def test_quota_uses_atomic_condition():
    client = MagicMock()
    client.update_item.return_value = {
        "Attributes": {"request_count": {"N": "5"}}
    }

    quota.reserve_request(client, "test-reports", 100)

    kwargs = client.update_item.call_args.kwargs

    assert "ConditionExpression" in kwargs
    assert "if_not_exists" in kwargs["UpdateExpression"]
    assert kwargs["ExpressionAttributeValues"][":limit"] == {"N": "100"}
