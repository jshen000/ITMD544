"""Offline execution of generated EC2 bootstrap scripts."""

import ast
import base64
import io
import json
import lzma
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from stack.artifacts import build_artifact
from stack.compute import build_user_data


def unpack_script(wrapper):
    tree = ast.parse(wrapper)

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "b64decode"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            return lzma.decompress(
                base64.b64decode(node.args[0].value)
            ).decode()

    raise AssertionError("Compressed bootstrap not found")


@pytest.mark.parametrize("tier_name", ["weather", "advisor"])
def test_ec2_bootstrap_execution(
    fake_settings, tier_name, tmp_path, monkeypatch
):
    tier = getattr(fake_settings, tier_name)
    artifact = build_artifact(tier)

    wrapper = build_user_data(
        fake_settings, tier, artifact=artifact
    )

    inner = unpack_script(wrapper)

    root = tmp_path / "weather-advisor"
    hook = tmp_path / "per-boot" / "weather-advisor.py"
    source_file = tmp_path / "cloud-init.py"
    source_file.write_text(wrapper)

    class FakeS3:
        def get_object(self, Bucket, Key):
            assert Bucket == fake_settings.bucket_name
            assert Key == artifact.key
            return {"Body": io.BytesIO(artifact.data)}

    fake_s3 = FakeS3()

    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = lambda service, region_name=None: (
        fake_s3 if service == "s3" else None
    )

    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    # Prevent actual package installation and process supervision.
    calls = []

    original_path = Path

    def RedirectPath(*args, **kwargs):
        if args:
            value = str(args[0])

            if value == "/opt/weather-advisor":
                args = (root,) + args[1:]

            elif value == (
                "/var/lib/cloud/scripts/per-boot/weather-advisor.py"
            ):
                args = (hook,) + args[1:]

        return original_path(*args, **kwargs)

    namespace = {
        "__name__": "bootstrap_test",
        "__file__": str(source_file),
    }

    exec(compile(inner, "bootstrap.py", "exec"), namespace)

    # Replace only the bootstrap namespace's Path constructor.
    namespace["Path"] = RedirectPath

    if True:
        namespace["install_dependencies"] = lambda: calls.append(
            "dependencies"
        )
        namespace["supervise_application"] = (
            lambda destination: calls.append(
                ("supervisor", str(destination))
            )
        )

        namespace["main"]()

    assert (root / "app.py").is_file()

    config = json.loads((root / "config.json").read_text())
    assert config["TIER"] == tier_name
    assert config["AWS_REGION"] == fake_settings.region

    assert hook.is_file()
    assert hook.read_text() == wrapper

    assert calls == [
        "dependencies",
        ("supervisor", str(root)),
    ]
