"""Offline verification of EC2 artifact downloads."""

import hashlib
import io
import tarfile
from unittest.mock import MagicMock

import pytest

from stack.artifacts import build_artifact
from stack.artifact_bootstrap import install_artifact


def fake_s3(data):
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": io.BytesIO(data)}
    return s3


def test_verified_weather_install(fake_settings, tmp_path):
    artifact = build_artifact(fake_settings.weather)
    s3 = fake_s3(artifact.data)

    root = install_artifact(
        s3,
        fake_settings.bucket_name,
        artifact.key,
        artifact.sha256,
        tmp_path / "weather",
    )

    assert (root / "app.py").is_file()
    s3.get_object.assert_called_once_with(
        Bucket=fake_settings.bucket_name,
        Key=artifact.key,
    )


def test_verified_advisor_install(fake_settings, tmp_path):
    artifact = build_artifact(fake_settings.advisor)

    root = install_artifact(
        fake_s3(artifact.data),
        fake_settings.bucket_name,
        artifact.key,
        artifact.sha256,
        tmp_path / "advisor",
    )

    assert (root / "app.py").is_file()


def test_checksum_mismatch_rejected(fake_settings, tmp_path):
    artifact = build_artifact(fake_settings.weather)

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        install_artifact(
            fake_s3(artifact.data + b"corrupt"),
            fake_settings.bucket_name,
            artifact.key,
            artifact.sha256,
            tmp_path / "weather",
        )

    assert not (tmp_path / "weather").exists()


def test_path_traversal_rejected(tmp_path):
    buffer = io.BytesIO()

    with tarfile.open(fileobj=buffer, mode="w:xz") as archive:
        data = b"print('unsafe')"
        info = tarfile.TarInfo("../outside.py")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))

    content = buffer.getvalue()
    digest = hashlib.sha256(content).hexdigest()

    with pytest.raises(ValueError, match="Unsafe archive member"):
        install_artifact(
            fake_s3(content),
            "test-bucket",
            "artifacts/weather/test.tar.xz",
            digest,
            tmp_path / "weather",
        )

    assert not (tmp_path / "outside.py").exists()
