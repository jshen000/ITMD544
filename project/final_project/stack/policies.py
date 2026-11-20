"""Least-privilege IAM policies for weather and advisor."""

from __future__ import annotations


def trust_policy() -> dict:
    """Allow EC2 to assume the instance role."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "ec2.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }


def weather_policy(facts_table_arn: str) -> dict:
    """Allow weather to read and update its DynamoDB table."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "WeatherFacts",
                "Effect": "Allow",
                "Action": [
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                ],
                "Resource": facts_table_arn,
            }
        ],
    }


def advisor_policy(
    reports_table_arn: str,
    gemini_parameter_arn: str,
) -> dict:
    """Allow advisor to manage reports and read the Gemini key."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ReportsAndCounters",
                "Effect": "Allow",
                "Action": [
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                ],
                "Resource": reports_table_arn,
            },
            {
                "Sid": "ReadGeminiKey",
                "Effect": "Allow",
                "Action": ["ssm:GetParameter"],
                "Resource": gemini_parameter_arn,
            },
        ],
    }


def policy_for(
    tier_name: str,
    *,
    facts_table_arn: str,
    reports_table_arn: str,
    gemini_parameter_arn: str,
    bucket_name: str | None = None,
    partition: str = "aws",
) -> dict:
    """Return the least-privilege policy for one application tier."""
    if tier_name == "weather":
        policy = weather_policy(facts_table_arn)
    elif tier_name == "advisor":
        policy = advisor_policy(
            reports_table_arn,
            gemini_parameter_arn,
        )
    else:
        raise ValueError(f"Unknown tier: {tier_name}")

    if bucket_name is not None:
        policy["Statement"].append(
            artifact_read_statement(
                tier_name,
                bucket_name,
                partition,
            )
        )

    return policy


def artifact_read_statement(
    tier_name: str,
    bucket_name: str,
    partition: str = "aws",
) -> dict:
    """Allow one EC2 tier to read only its own deployment archives."""
    if tier_name not in ("weather", "advisor"):
        raise ValueError(f"Unknown tier: {tier_name}")

    if not bucket_name:
        raise ValueError("Artifact bucket name is required")

    return {
        "Sid": "ReadApplicationArtifacts",
        "Effect": "Allow",
        "Action": ["s3:GetObject"],
        "Resource": (
            f"arn:{partition}:s3:::{bucket_name}/"
            f"artifacts/{tier_name}/*"
        ),
    }
