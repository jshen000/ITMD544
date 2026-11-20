"""Download and safely extract a verified application archive."""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path


def install_artifact(
    s3,
    bucket_name: str,
    key: str,
    expected_sha256: str,
    destination: Path,
) -> Path:
    """Install a verified S3 artifact without extracting unsafe members."""
    destination = Path(destination)

    if len(expected_sha256) != 64 or any(
        char not in "0123456789abcdef" for char in expected_sha256.lower()
    ):
        raise ValueError("Invalid SHA-256 checksum")

    response = s3.get_object(Bucket=bucket_name, Key=key)
    body = response["Body"].read()

    if len(body) > 10 * 1024 * 1024:
        raise ValueError("Application archive exceeds 10 MiB")

    actual_sha256 = hashlib.sha256(body).hexdigest()
    if actual_sha256 != expected_sha256.lower():
        raise ValueError("Artifact SHA-256 mismatch")

    extracted = {}

    with tarfile.open(fileobj=io.BytesIO(body), mode="r:xz") as archive:
        for member in archive.getmembers():
            path = Path(member.name)

            if (
                path.is_absolute()
                or ".." in path.parts
                or not member.isfile()
                or path.suffix != ".py"
                or member.name in extracted
            ):
                raise ValueError(f"Unsafe archive member: {member.name}")

            if member.size > 2 * 1024 * 1024:
                raise ValueError("Archive member exceeds 2 MiB")

            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"Cannot read archive member: {member.name}")

            content = source.read(2 * 1024 * 1024 + 1)
            if len(content) != member.size:
                raise ValueError("Archive member size mismatch")

            extracted[member.name] = content

    if "app.py" not in extracted:
        raise ValueError("Application archive is missing app.py")

    destination.mkdir(parents=True, exist_ok=True)

    for filename, content in extracted.items():
        target = destination / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o644)

    return destination
