"""Least-privilege IAM tests for deployment artifacts."""

import pytest

from stack.policies import artifact_read_statement


@pytest.mark.parametrize("tier", ["weather", "advisor"])
def test_tier_can_read_only_its_artifacts(tier):
    statement = artifact_read_statement(
        tier, "test-final-artifacts"
    )

    assert statement["Effect"] == "Allow"
    assert statement["Action"] == ["s3:GetObject"]
    assert statement["Resource"] == (
        f"arn:aws:s3:::test-final-artifacts/artifacts/{tier}/*"
    )


def test_unknown_tier_rejected():
    with pytest.raises(ValueError):
        artifact_read_statement("unknown", "test-final-artifacts")


def test_empty_bucket_rejected():
    with pytest.raises(ValueError):
        artifact_read_statement("weather", "")
