"""Least-privilege IAM policy tests for Weather and Advisor."""

import pytest

from stack.policies import policy_for, trust_policy


FACTS_ARN = (
    "arn:aws:dynamodb:us-east-2:123456789012:table/test-facts"
)
REPORTS_ARN = (
    "arn:aws:dynamodb:us-east-2:123456789012:table/test-reports"
)
GEMINI_ARN = (
    "arn:aws:ssm:us-east-2:123456789012:"
    "parameter/final-test/gemini-api-key"
)


def make_policy(tier):
    return policy_for(
        tier,
        facts_table_arn=FACTS_ARN,
        reports_table_arn=REPORTS_ARN,
        gemini_parameter_arn=GEMINI_ARN,
    )


def test_trust_policy_only_trusts_ec2():
    statement = trust_policy()["Statement"][0]

    assert statement["Principal"] == {
        "Service": "ec2.amazonaws.com"
    }
    assert statement["Action"] == "sts:AssumeRole"


def test_weather_has_only_facts_table_permissions():
    statements = make_policy("weather")["Statement"]

    assert len(statements) == 1
    assert statements[0]["Resource"] == FACTS_ARN

    assert set(statements[0]["Action"]) == {
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
    }


def test_advisor_has_reports_and_gemini_permissions():
    statements = make_policy("advisor")["Statement"]

    assert len(statements) == 2

    assert statements[0]["Resource"] == REPORTS_ARN
    assert set(statements[0]["Action"]) == {
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
    }

    assert statements[1]["Resource"] == GEMINI_ARN
    assert statements[1]["Action"] == ["ssm:GetParameter"]


def test_no_policy_uses_a_wildcard_resource():
    for tier in ("weather", "advisor"):
        for statement in make_policy(tier)["Statement"]:
            assert statement["Resource"] != "*"
            assert statement["Effect"] == "Allow"


def test_unknown_tier_is_rejected():
    with pytest.raises(ValueError):
        make_policy("uploader")
