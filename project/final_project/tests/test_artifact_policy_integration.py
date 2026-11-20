"""Verify S3 permissions are integrated without losing existing access."""

import json
from unittest.mock import MagicMock

import pytest

from stack.policies import policy_for
from stack.storage import ensure_role_and_profile


@pytest.mark.parametrize("tier", ["weather", "advisor"])
def test_policy_includes_artifact_read_access(tier):
    policy = policy_for(
        tier,
        facts_table_arn="arn:aws:dynamodb:us-east-2:123:table/facts",
        reports_table_arn="arn:aws:dynamodb:us-east-2:123:table/reports",
        gemini_parameter_arn="arn:aws:ssm:us-east-2:123:parameter/key",
        bucket_name="test-final-artifacts",
    )

    statements = {s["Sid"]: s for s in policy["Statement"]}

    assert "ReadApplicationArtifacts" in statements
    assert statements["ReadApplicationArtifacts"]["Action"] == ["s3:GetObject"]
    assert statements["ReadApplicationArtifacts"]["Resource"] == (
        f"arn:aws:s3:::test-final-artifacts/artifacts/{tier}/*"
    )

    if tier == "weather":
        assert "WeatherFacts" in statements
        assert "ReadGeminiKey" not in statements
    else:
        assert "ReportsAndCounters" in statements
        assert "ReadGeminiKey" in statements


def test_role_receives_s3_permissions(fake_settings):
    aws = MagicMock()
    aws.iam.get_instance_profile.return_value = {
        "InstanceProfile": {"Roles": []}
    }

    ensure_role_and_profile(
        aws,
        fake_settings,
        fake_settings.weather,
        facts_table_arn="arn:aws:dynamodb:us-east-2:123:table/facts",
        reports_table_arn="arn:aws:dynamodb:us-east-2:123:table/reports",
        gemini_parameter_arn="arn:aws:ssm:us-east-2:123:parameter/key",
    )

    kwargs = aws.iam.put_role_policy.call_args.kwargs
    policy = json.loads(kwargs["PolicyDocument"])

    resources = [
        s["Resource"]
        for s in policy["Statement"]
        if s.get("Sid") == "ReadApplicationArtifacts"
    ]

    assert resources == [
        "arn:aws:s3:::test-final-artifacts/artifacts/weather/*"
    ]
