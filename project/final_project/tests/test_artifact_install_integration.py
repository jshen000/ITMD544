"""Offline S3 artifact installation integration tests."""

from io import BytesIO
from pathlib import Path

import pytest

from stack.artifacts import build_artifact
from stack.artifact_bootstrap import install_artifact


class FakeS3:
    def __init__(self, artifact):
        self.artifact = artifact
        self.calls = []

    def get_object(self, Bucket, Key):
        self.calls.append((Bucket, Key))
        assert Key == self.artifact.key
        return {"Body": BytesIO(self.artifact.data)}


@pytest.mark.parametrize("tier_name", ["weather", "advisor"])
def test_install_application_artifact(
    fake_settings, tier_name, tmp_path
):
    tier = getattr(fake_settings, tier_name)
    artifact = build_artifact(tier)
    s3 = FakeS3(artifact)

    destination = tmp_path / tier_name

    install_artifact(
        s3,
        fake_settings.bucket_name,
        artifact.key,
        artifact.sha256,
        destination,
    )

    assert (destination / "app.py").is_file()
    assert s3.calls == [
        (fake_settings.bucket_name, artifact.key)
    ]


@pytest.mark.parametrize("tier_name", ["weather", "advisor"])
def test_reject_corrupted_artifact(
    fake_settings, tier_name, tmp_path
):
    tier = getattr(fake_settings, tier_name)
    artifact = build_artifact(tier)
    s3 = FakeS3(artifact)

    with pytest.raises(ValueError):
        install_artifact(
            s3,
            fake_settings.bucket_name,
            artifact.key,
            "0" * 64,
            tmp_path / tier_name,
        )
