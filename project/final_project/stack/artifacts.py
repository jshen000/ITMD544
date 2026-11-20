"""Build deterministic, versioned application archives."""

from __future__ import annotations

import hashlib
import io
import tarfile
from dataclasses import dataclass
from pathlib import Path

from .config import Tier


@dataclass(frozen=True)
class Artifact:
    tier: str
    key: str
    sha256: str
    data: bytes


def build_artifact(tier: Tier) -> Artifact:
    """Package runtime Python files without tests or caches."""

    if not (tier.app_dir / "app.py").is_file():
        raise FileNotFoundError(
            f"Missing application entry point: {tier.app_dir / 'app.py'}"
        )

    buffer = io.BytesIO()

    with tarfile.open(fileobj=buffer, mode="w:xz") as archive:
        for path in sorted(tier.app_dir.rglob("*.py")):
            relative = path.relative_to(tier.app_dir)

            if any(
                part == "__pycache__"
                or part == "tests"
                or part.startswith("test_")
                for part in relative.parts
            ):
                continue

            data = path.read_bytes()
            info = tarfile.TarInfo(relative.as_posix())
            info.size = len(data)
            info.mode = 0o644
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""

            archive.addfile(info, io.BytesIO(data))

    content = buffer.getvalue()
    digest = hashlib.sha256(content).hexdigest()

    return Artifact(
        tier=tier.name,
        key=f"artifacts/{tier.name}/{digest}.tar.xz",
        sha256=digest,
        data=content,
    )


def upload_artifact(s3, bucket_name: str, artifact: Artifact) -> str:
    """Upload a content-addressed archive to private S3."""
    from botocore.exceptions import ClientError

    try:
        s3.head_object(Bucket=bucket_name, Key=artifact.key)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "")
        if code not in ("404", "NoSuchKey", "NotFound"):
            raise
    else:
        return artifact.key

    s3.put_object(
        Bucket=bucket_name,
        Key=artifact.key,
        Body=artifact.data,
        ContentType="application/x-xz",
        Metadata={"sha256": artifact.sha256},
    )

    return artifact.key


def upload_tier_artifacts(s3, bucket_name: str, tiers) -> dict[str, Artifact]:
    """Build and upload each application archive."""
    result = {}

    for tier in tiers:
        artifact = build_artifact(tier)
        upload_artifact(s3, bucket_name, artifact)
        result[tier.name] = artifact

    return result
