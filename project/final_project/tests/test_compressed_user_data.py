"""Verify both layers of compressed EC2 user-data."""

import ast
import base64
import lzma

import pytest

from stack.artifacts import build_artifact
from stack.compute import build_user_data


def extract_compressed_sources(script):
    """Find and decompress LZMA/base64 constants without executing code."""
    tree = ast.parse(script)
    sources = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        if not isinstance(node.func, ast.Attribute):
            continue

        if node.func.attr != "b64decode" or not node.args:
            continue

        argument = node.args[0]

        if not isinstance(argument, ast.Constant):
            continue

        if not isinstance(argument.value, str):
            continue

        try:
            data = base64.b64decode(
                argument.value,
                validate=True,
            )
            decoded = lzma.decompress(data).decode()
        except (ValueError, lzma.LZMAError, UnicodeError):
            continue

        sources.append(decoded)

    return sources


@pytest.mark.parametrize("tier_name", ["weather", "advisor"])
def test_s3_bootstrap_script(fake_settings, tier_name):
    tier = getattr(fake_settings, tier_name)
    artifact = build_artifact(tier)

    wrapper = build_user_data(
        fake_settings,
        tier,
        artifact=artifact,
    )

    assert wrapper.startswith("#!/usr/bin/python3")
    assert len(wrapper.encode()) <= 16384

    outer_sources = extract_compressed_sources(wrapper)

    assert len(outer_sources) == 1

    inner = outer_sources[0]
    compile(inner, f"{tier_name}-bootstrap.py", "exec")

    assert "install_artifact(" in inner
    assert "supervise_application(root)" in inner
    assert "config.json" in inner

    inner_sources = extract_compressed_sources(inner)

    downloader_sources = [
        source
        for source in inner_sources
        if "def install_artifact(" in source
    ]

    assert len(downloader_sources) == 1

    downloader = downloader_sources[0]
    compile(downloader, "artifact_bootstrap.py", "exec")

    assert "hashlib.sha256" in downloader
    assert "Unsafe archive member" in downloader

    import json

    payload_sources = [
        source
        for source in inner_sources
        if '"artifact"' in source and '"config"' in source
    ]

    assert len(payload_sources) == 1

    payload = json.loads(payload_sources[0])

    assert payload["artifact"]["key"] == artifact.key
    assert payload["artifact"]["sha256"] == artifact.sha256
    assert payload["artifact"]["bucket"] == fake_settings.bucket_name
    assert payload["config"]["TIER"] == tier_name
