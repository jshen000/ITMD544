"""Verify S3 artifacts are wired into real Moto launch templates."""

import ast
import base64
import json
import lzma

from stack.artifacts import build_artifact
from stack.runner import create_stack
from tests.test_stack_with_moto import env


def decoded_strings(source):
    """Recursively extract LZMA/base64-encoded source strings."""
    results = []
    pending = [source]

    while pending:
        current = pending.pop()
        tree = ast.parse(current)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "b64decode" or not node.args:
                continue

            value = node.args[0]

            if not isinstance(value, ast.Constant):
                continue
            if not isinstance(value.value, str):
                continue

            try:
                decoded = lzma.decompress(
                    base64.b64decode(value.value, validate=True)
                ).decode()
            except (ValueError, lzma.LZMAError, UnicodeError):
                continue

            results.append(decoded)

            try:
                ast.parse(decoded)
            except SyntaxError:
                continue
            else:
                pending.append(decoded)

    return results


def test_launch_templates_reference_uploaded_artifacts(env):
    settings, aws = env

    create_stack(
        settings,
        aws,
        iam_wait=0,
        sleep=lambda _: None,
    )

    for tier in settings.tiers:
        expected = build_artifact(tier)

        # The actual artifact must exist in S3.
        response = aws.s3.get_object(
            Bucket=settings.bucket_name,
            Key=expected.key,
        )

        assert response["Body"].read() == expected.data

        # Inspect the actual EC2 launch template.
        response = aws.ec2.describe_launch_template_versions(
            LaunchTemplateName=tier.lt_name,
            Versions=["$Latest"],
        )

        version = response["LaunchTemplateVersions"][0]
        user_data = base64.b64decode(
            version["LaunchTemplateData"]["UserData"]
        ).decode()

        assert len(user_data.encode()) <= 16384

        sources = decoded_strings(user_data)

        payloads = []
        for source in sources:
            try:
                value = json.loads(source)
            except (ValueError, TypeError):
                continue

            if isinstance(value, dict) and "artifact" in value:
                payloads.append(value)

        assert len(payloads) == 1

        artifact = payloads[0]["artifact"]

        assert artifact["bucket"] == settings.bucket_name
        assert artifact["key"] == expected.key
        assert artifact["sha256"] == expected.sha256

        # Check the EC2 role can read only its own tier's artifacts.
        role_policy = aws.iam.get_role_policy(
            RoleName=tier.role_name,
            PolicyName=tier.policy_name,
        )["PolicyDocument"]

        expected_resource = (
            f"arn:aws:s3:::{settings.bucket_name}/"
            f"artifacts/{tier.name}/*"
        )

        s3_statements = [
            statement
            for statement in role_policy["Statement"]
            if "s3:GetObject" in (
                [statement["Action"]]
                if isinstance(statement["Action"], str)
                else statement["Action"]
            )
        ]

        assert len(s3_statements) == 1
        assert s3_statements[0]["Effect"] == "Allow"
        assert s3_statements[0]["Resource"] == expected_resource

        print(f"PASS: {tier.name} S3 artifact and launch template")
