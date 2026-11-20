"""Offline tests for uploading both application tiers."""

from unittest.mock import MagicMock, patch

from stack.artifacts import upload_tier_artifacts


def test_uploads_both_tiers(fake_settings):
    s3 = MagicMock()

    with patch("stack.artifacts.upload_artifact") as upload:
        artifacts = upload_tier_artifacts(
            s3,
            fake_settings.bucket_name,
            fake_settings.tiers,
        )

    assert set(artifacts) == {"weather", "advisor"}
    assert upload.call_count == 2

    for name, artifact in artifacts.items():
        assert artifact.tier == name
        assert artifact.key.startswith(f"artifacts/{name}/")
        assert len(artifact.sha256) == 64
