"""S3 bucket and IAM role and instance profile: the foundation of the stack."""

from __future__ import annotations

import json
import logging

from botocore.exceptions import ClientError

from .clients import AwsClients
from .config import Settings, Tier
from .policies import policy_for, trust_policy
from .util import error_code, ignore_errors


log = logging.getLogger("stack")


def ensure_bucket(aws: AwsClients, settings: Settings) -> None:
    try:
        aws.s3.head_bucket(Bucket=settings.bucket_name)
        log.info("Bucket %s already exists, skipping.", settings.bucket_name)
    except ClientError as err:
        # Stop for errors other than a missing bucket.
        if error_code(err) not in ("404", "NoSuchBucket", "NotFound"):
            raise

        args = {"Bucket": settings.bucket_name}

        if settings.region != "us-east-1":
            args["CreateBucketConfiguration"] = {
                "LocationConstraint": settings.region
            }

        aws.s3.create_bucket(**args)
        log.info("Created bucket %s", settings.bucket_name)

    aws.s3.put_public_access_block(
        Bucket=settings.bucket_name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )


def ensure_role_and_profile(
    aws: AwsClients,
    settings: Settings,
    tier: Tier,
    *,
    facts_table_arn: str,
    reports_table_arn: str,
    gemini_parameter_arn: str,
    partition: str = "aws",
) -> None:
    """Create the role, its inline policy, and its instance profile."""
    with ignore_errors("EntityAlreadyExists"):
        aws.iam.create_role(
            RoleName=tier.role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy()),
        )

    aws.iam.put_role_policy(
        RoleName=tier.role_name,
        PolicyName=tier.policy_name,
        PolicyDocument=json.dumps(policy_for(
            tier.name,
            facts_table_arn=facts_table_arn,
            reports_table_arn=reports_table_arn,
            gemini_parameter_arn=gemini_parameter_arn,
            bucket_name=settings.bucket_name,
            partition=partition,
        )),
    )

    with ignore_errors("EntityAlreadyExists"):
        aws.iam.create_instance_profile(
            InstanceProfileName=tier.profile_name
        )

    profile = aws.iam.get_instance_profile(
        InstanceProfileName=tier.profile_name
    )
    roles = [
        role["RoleName"]
        for role in profile["InstanceProfile"]["Roles"]
    ]

    if tier.role_name not in roles:
        aws.iam.add_role_to_instance_profile(
            InstanceProfileName=tier.profile_name,
            RoleName=tier.role_name,
        )

    log.info("%s role and instance profile are ready.", tier.name)


def delete_role_and_profile(aws: AwsClients, tier: Tier) -> None:
    """Remove the instance profile, role policies, and role."""
    with ignore_errors("NoSuchEntity"):
        aws.iam.remove_role_from_instance_profile(
            InstanceProfileName=tier.profile_name,
            RoleName=tier.role_name,
        )

    with ignore_errors("NoSuchEntity"):
        aws.iam.delete_instance_profile(
            InstanceProfileName=tier.profile_name
        )

    # Collect all inline policy names before deleting them.
    inline_names = []

    with ignore_errors("NoSuchEntity"):
        paginator = aws.iam.get_paginator("list_role_policies")

        for page in paginator.paginate(RoleName=tier.role_name):
            inline_names.extend(page["PolicyNames"])

    for name in inline_names:
        with ignore_errors("NoSuchEntity"):
            aws.iam.delete_role_policy(
                RoleName=tier.role_name,
                PolicyName=name,
            )

    # Detach managed policies without deleting the policies themselves.
    attached_policies = []

    with ignore_errors("NoSuchEntity"):
        paginator = aws.iam.get_paginator("list_attached_role_policies")

        for page in paginator.paginate(RoleName=tier.role_name):
            attached_policies.extend(page["AttachedPolicies"])

    for policy in attached_policies:
        with ignore_errors("NoSuchEntity"):
            aws.iam.detach_role_policy(
                RoleName=tier.role_name,
                PolicyArn=policy["PolicyArn"],
            )

    with ignore_errors("NoSuchEntity"):
        aws.iam.delete_role(RoleName=tier.role_name)

    log.info("Removed %s role and instance profile.", tier.name)


def delete_bucket(aws: AwsClients, settings: Settings) -> None:
    with ignore_errors("NoSuchBucket"):
        paginator = aws.s3.get_paginator("list_objects_v2")

        for page in paginator.paginate(Bucket=settings.bucket_name):
            objects = [
                {"Key": obj["Key"]}
                for obj in page.get("Contents", [])
            ]

            if objects:
                aws.s3.delete_objects(
                    Bucket=settings.bucket_name,
                    Delete={"Objects": objects},
                )

        aws.s3.delete_bucket(Bucket=settings.bucket_name)
        log.info("Deleted bucket %s", settings.bucket_name)


def ensure_table(aws: AwsClients, table_name: str) -> str:
    """Ensure an on-demand table with a string pk and expires TTL."""
    client = aws.dynamodb

    try:
        client.describe_table(TableName=table_name)
    except ClientError as err:
        if error_code(err) != "ResourceNotFoundException":
            raise

        client.create_table(
            TableName=table_name,
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        log.info("Created DynamoDB table %s", table_name)

    client.get_waiter("table_exists").wait(TableName=table_name)

    table = client.describe_table(TableName=table_name)["Table"]

    if table["KeySchema"] != [{"AttributeName": "pk", "KeyType": "HASH"}]:
        raise ValueError(f"Unexpected key schema for {table_name}")

    definitions = {
        item["AttributeName"]: item["AttributeType"]
        for item in table["AttributeDefinitions"]
    }
    if definitions.get("pk") != "S":
        raise ValueError(f"Expected a string pk for {table_name}")

    mode = table.get("BillingModeSummary", {}).get("BillingMode")
    if mode != "PAY_PER_REQUEST":
        raise ValueError(f"Expected on-demand billing for {table_name}")

    ttl = client.describe_time_to_live(
        TableName=table_name
    )["TimeToLiveDescription"]

    status = ttl.get("TimeToLiveStatus", "DISABLED")

    if status == "DISABLED":
        client.update_time_to_live(
            TableName=table_name,
            TimeToLiveSpecification={
                "Enabled": True,
                "AttributeName": "expires",
            },
        )
    elif status in ("ENABLED", "ENABLING"):
        if ttl.get("AttributeName") != "expires":
            raise ValueError(f"Unexpected TTL attribute for {table_name}")
    else:
        raise RuntimeError(f"TTL is changing for {table_name}; retry later")

    log.info("DynamoDB table %s is ready", table_name)
    return table["TableArn"]


def delete_table(aws: AwsClients, table_name: str) -> None:
    """Delete a table safely, including when it is already absent."""
    client = aws.dynamodb

    try:
        table = client.describe_table(
            TableName=table_name
        )["Table"]
    except ClientError as err:
        if error_code(err) != "ResourceNotFoundException":
            raise
        log.info("Table %s is already absent", table_name)
        return

    if table["TableStatus"] != "DELETING":
        try:
            client.delete_table(TableName=table_name)
        except ClientError as err:
            if error_code(err) != "ResourceNotFoundException":
                raise

    client.get_waiter("table_not_exists").wait(
        TableName=table_name
    )
    log.info("Deleted DynamoDB table %s", table_name)
