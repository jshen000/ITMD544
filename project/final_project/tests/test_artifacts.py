"""Offline tests for application artifact packaging."""

import hashlib
import io
import tarfile

from stack.artifacts import build_artifact


def test_artifact_contains_application(fake_settings):
    artifact = build_artifact(fake_settings.weather)

    assert artifact.key.startswith("artifacts/weather/")
    assert artifact.key.endswith(".tar.xz")
    assert hashlib.sha256(artifact.data).hexdigest() == artifact.sha256

    with tarfile.open(
        fileobj=io.BytesIO(artifact.data), mode="r:xz"
    ) as archive:
        assert "app.py" in archive.getnames()


def test_artifact_is_deterministic(fake_settings):
    first = build_artifact(fake_settings.advisor)
    second = build_artifact(fake_settings.advisor)

    assert first.sha256 == second.sha256
    assert first.data == second.data


def test_artifact_excludes_tests(fake_settings):
    folder = fake_settings.weather.app_dir

    (folder / "test_example.py").write_text("assert False\n")
    (folder / "__pycache__").mkdir()
    (folder / "__pycache__" / "cached.py").write_text("bad\n")

    artifact = build_artifact(fake_settings.weather)

    with tarfile.open(
        fileobj=io.BytesIO(artifact.data), mode="r:xz"
    ) as archive:
        names = archive.getnames()

    assert "app.py" in names
    assert "test_example.py" not in names
    assert not any("__pycache__" in name for name in names)
