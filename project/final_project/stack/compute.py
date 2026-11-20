"""User-data and launch templates."""

from __future__ import annotations

import base64
import logging

from botocore.exceptions import ClientError

from .clients import AwsClients
from .config import Settings, Tier
from .util import error_code


log = logging.getLogger("stack")


def _b64_file(path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def build_user_data(
    settings: Settings,
    tier: Tier,
    weather_url: str | None = None,
    artifact=None,
) -> str:
    """Generate Python-only EC2 user-data from the boot template."""
    import lzma
    import json
    from pathlib import Path

    entry = tier.app_dir / "app.py"
    if not entry.is_file():
        raise FileNotFoundError(f"Missing Python application: {entry}")

    files = {}
    for path in sorted(tier.app_dir.rglob("*.py")):
        relative_path = path.relative_to(tier.app_dir)

        if "__pycache__" in relative_path.parts:
            continue

        if any(
            part == "tests" or part.startswith("test_")
            for part in relative_path.parts
        ):
            continue

        relative = relative_path.as_posix()
        files[relative] = _b64_file(path)

    config = {
        "AWS_REGION": settings.region,
        "PORT": settings.app_port,
        "TIER": tier.name,
    }

    if tier.name == "weather":
        config["FACTS_TABLE_NAME"] = settings.facts_table_name
    elif tier.name == "advisor":
        config["REPORTS_TABLE_NAME"] = settings.reports_table_name
        config["GEMINI_PARAM_NAME"] = settings.gemini_param_name
        if weather_url:
            config["WEATHER_URL"] = weather_url
    else:
        raise ValueError(f"Unknown tier: {tier.name}")

    deployment = {"files": files, "config": config}

    if artifact is not None:
        deployment["artifact"] = {
            "bucket": settings.bucket_name,
            "key": artifact.key,
            "sha256": artifact.sha256,
        }

    payload = json.dumps(deployment, sort_keys=True).encode()

    encoded = base64.b64encode(
        lzma.compress(payload, preset=6)
    ).decode()

    template = Path(__file__).with_name(
        "boot_template.py"
    ).read_text()

    marker = "PAYLOAD = None  # Filled by build_user_data."
    if template.count(marker) != 1:
        raise ValueError("Expected one payload marker in boot template")

    injected = (
        "import lzma\n"
        "PAYLOAD = json.loads(lzma.decompress("
        f"base64.b64decode({encoded!r})))"
    )
    script = template.replace(marker, injected, 1)

    if artifact is not None:
        downloader = Path(__file__).with_name(
            "artifact_bootstrap.py"
        ).read_text()

        downloader = downloader.replace(
            "from __future__ import annotations", "", 1
        )

        compressed = base64.b64encode(
            lzma.compress(downloader.encode(), preset=6)
        ).decode()

        loader = (
            "exec(compile(lzma.decompress("
            f"base64.b64decode({compressed!r})), "
            "'artifact_bootstrap.py', 'exec'))"
        )

        anchor = "def prepare_files(payload):"

        if script.count(anchor) != 1:
            raise ValueError("Bootstrap insertion point not found")

        script = script.replace(
            anchor,
            loader + "\n\n" + anchor,
            1,
        )

    if artifact is not None:
        start = script.index("def prepare_files(payload):")
        end = script.index("def supervise_application(root):", start)

        script = script[:start] + script[end:]

    compile(script, "ec2-user-data.py", "exec")

    if artifact is not None:
        compressed_script = base64.b64encode(
            lzma.compress(script.encode(), preset=6)
        ).decode()

        script = (
            "#!/usr/bin/python3\n"
            "import base64, lzma\n"
            "exec(compile(lzma.decompress("
            f"base64.b64decode({compressed_script!r})), "
            "'ec2-user-data.py', 'exec'))\n"
        )

    if len(script.encode()) > 16384:
        raise ValueError("EC2 user-data exceeds the 16 KiB limit")

    return script


def launch_template_data(
    settings: Settings,
    tier: Tier,
    weather_url: str | None = None,
    artifact=None,
) -> dict:
    user_data = build_user_data(settings, tier, weather_url, artifact=artifact)

    return {
        "ImageId": settings.ami_id,
        "InstanceType": settings.instance_type,
        "KeyName": settings.key_name,
        "IamInstanceProfile": {"Name": tier.profile_name},
        "NetworkInterfaces": [
            {
                "DeviceIndex": 0,
                "AssociatePublicIpAddress": True,
                "Groups": [settings.app_sg_id],
            }
        ],
        "UserData": base64.b64encode(user_data.encode()).decode(),
        "TagSpecifications": [
            {
                "ResourceType": "instance",
                "Tags": [{"Key": "Name", "Value": f"{tier.name}-app"}],
            }
        ],
    }



def launch_template_exists(aws: AwsClients, tier: Tier) -> bool:
    try:
        aws.ec2.describe_launch_templates(
            LaunchTemplateNames=[tier.lt_name]
        )
        return True
    except ClientError as err:
        if error_code(err) != "InvalidLaunchTemplateName.NotFoundException":
            raise
        return False


def ensure_launch_template(
    aws: AwsClients,
    settings: Settings,
    tier: Tier,
    weather_url: str | None = None,
    artifact=None,
) -> int:
    """Reuse the latest version when launch configuration is unchanged."""
    data = launch_template_data(settings, tier, weather_url, artifact=artifact)

    if not launch_template_exists(aws, tier):
        response = aws.ec2.create_launch_template(
            LaunchTemplateName=tier.lt_name,
            LaunchTemplateData=data,
        )
        log.info("Created launch template %s", tier.lt_name)
        return response["LaunchTemplate"]["LatestVersionNumber"]

    latest = aws.ec2.describe_launch_template_versions(
        LaunchTemplateName=tier.lt_name,
        Versions=["$Latest"],
    )["LaunchTemplateVersions"][0]

    def matches(actual, desired):
        """Compare requested fields, allowing additional response fields."""
        if isinstance(desired, dict):
            return isinstance(actual, dict) and all(
                key in actual and matches(actual[key], value)
                for key, value in desired.items()
            )
        if isinstance(desired, list):
            return (
                isinstance(actual, list)
                and len(actual) == len(desired)
                and all(matches(a, b) for a, b in zip(actual, desired))
            )
        return actual == desired

    if matches(latest["LaunchTemplateData"], data):
        version = latest["VersionNumber"]
        log.info(
            "Launch template %s unchanged; reusing version %s",
            tier.lt_name,
            version,
        )
        return version

    response = aws.ec2.create_launch_template_version(
        LaunchTemplateName=tier.lt_name,
        LaunchTemplateData=data,
    )
    version = response["LaunchTemplateVersion"]["VersionNumber"]
    log.info("Updated %s to version %s", tier.lt_name, version)
    return version



def delete_launch_template(aws: AwsClients, tier: Tier) -> None:
    if launch_template_exists(aws, tier):
        aws.ec2.delete_launch_template(LaunchTemplateName=tier.lt_name)
        log.info("Deleted launch template %s", tier.lt_name)
