"""Run the real create and delete code against moto, an in-memory fake of AWS.

Nothing here touches your AWS account, costs money, or needs credentials.
"""
from pathlib import Path

import json

import pytest
from moto import mock_aws

from stack.clients import AwsClients
from stack.config import Settings
from stack.runner import create_stack, delete_stack
from tests.conftest import FAKE_VALUES


@pytest.fixture
def env(tmp_path, monkeypatch, fake_settings):
    """Isolated Moto AWS account for the Final Project."""
    monkeypatch.chdir(tmp_path)

    with mock_aws():
        aws = AwsClients.create("us-east-2")

        aws.ec2.create_key_pair(
            KeyName=fake_settings.key_name
        )

        ami = aws.ec2.describe_images()["Images"][0]["ImageId"]

        values = dict(
            FAKE_VALUES,
            APP_SECURITY_GROUP_ID="sg-placeholder",
            AMI_ID=ami,
        )

        settings = Settings.from_values(
            values,
            base_dir=fake_settings.weather.app_dir.parent,
        )

        yield settings, aws


def _app_sg_rules(aws, settings):
    group = aws.ec2.describe_security_groups(
        GroupIds=[settings.app_sg_id]
    )["SecurityGroups"][0]

    return [p for p in group["IpPermissions"] if p["FromPort"] == 3000]


def test_create_builds_every_resource(env):
    settings, aws = env

    outputs = create_stack(
        settings,
        aws,
        iam_wait=0,
        sleep=lambda _: None,
    )

    # Verify DynamoDB tables.
    for table_name in (
        settings.facts_table_name,
        settings.reports_table_name,
    ):
        table = aws.dynamodb.describe_table(
            TableName=table_name
        )["Table"]

        assert table["TableName"] == table_name
        assert table["KeySchema"] == [
            {"AttributeName": "pk", "KeyType": "HASH"}
        ]
        assert table["BillingModeSummary"]["BillingMode"] == (
            "PAY_PER_REQUEST"
        )

        ttl = aws.dynamodb.describe_time_to_live(
            TableName=table_name
        )["TimeToLiveDescription"]

        assert ttl["AttributeName"] == "expires"
        assert ttl["TimeToLiveStatus"] in (
            "ENABLED",
            "ENABLING",
        )

    # Verify IAM roles, policies, and instance profiles.
    for tier in settings.tiers:
        role_policy = aws.iam.get_role_policy(
            RoleName=tier.role_name,
            PolicyName=tier.policy_name,
        )

        assert role_policy["PolicyDocument"]["Statement"]

        profile = aws.iam.get_instance_profile(
            InstanceProfileName=tier.profile_name
        )

        assert [
            role["RoleName"]
            for role in profile["InstanceProfile"]["Roles"]
        ] == [tier.role_name]

        # Verify launch template.
        templates = aws.ec2.describe_launch_templates(
            LaunchTemplateNames=[tier.lt_name]
        )["LaunchTemplates"]

        assert len(templates) == 1

        # Verify Auto Scaling Group.
        groups = aws.autoscaling.describe_auto_scaling_groups(
            AutoScalingGroupNames=[tier.asg_name]
        )["AutoScalingGroups"]

        assert len(groups) == 1

        group = groups[0]

        assert (
            group["MinSize"],
            group["MaxSize"],
            group["DesiredCapacity"],
        ) == (1, 1, 1)

        assert group["HealthCheckType"] == "ELB"

        # Verify target group.
        tg = aws.elbv2.describe_target_groups(
            Names=[tier.tg_name]
        )["TargetGroups"][0]

        assert tg["HealthCheckIntervalSeconds"] == 10
        assert tg["UnhealthyThresholdCount"] == 2

    # Verify ALB listeners.
    listeners = aws.elbv2.describe_listeners(
        LoadBalancerArn=outputs.alb_arn
    )["Listeners"]

    assert sorted(
        item["Port"] for item in listeners
    ) == sorted(
        tier.listener_port for tier in settings.tiers
    )

    assert outputs.alb_dns_name

    print("PASS: DynamoDB tables")
    print("PASS: IAM roles and instance profiles")
    print("PASS: EC2 launch templates")
    print("PASS: Auto Scaling Groups")
    print("PASS: ALB target groups and listeners")



def test_app_security_group_only_accepts_the_alb(env):
    settings, aws = env

    outputs = create_stack(
        settings,
        aws,
        iam_wait=0,
        sleep=lambda _: None,
    )

    groups = aws.ec2.describe_security_groups(
        Filters=[
            {
                "Name": "group-name",
                "Values": [settings.app_sg_name],
            },
            {
                "Name": "vpc-id",
                "Values": [outputs.vpc_id],
            },
        ]
    )["SecurityGroups"]

    assert len(groups) == 1

    rules = groups[0]["IpPermissions"]

    assert len(rules) == 1

    rule = rules[0]

    assert rule["IpProtocol"] == "tcp"
    assert rule["FromPort"] == settings.app_port
    assert rule["ToPort"] == settings.app_port

    assert rule.get("IpRanges", []) == []
    assert rule.get("Ipv6Ranges", []) == []

    assert [
        pair["GroupId"]
        for pair in rule["UserIdGroupPairs"]
    ] == [outputs.alb_security_group_id]

    print("PASS: Dedicated application security group")
    print("PASS: TCP 3000 restricted to ALB")
    print("PASS: No public CIDR ingress")



def test_create_twice_does_not_duplicate_anything(env):
    settings, aws = env

    first = create_stack(
        settings,
        aws,
        iam_wait=0,
        sleep=lambda _: None,
    )

    second = create_stack(
        settings,
        aws,
        iam_wait=0,
        sleep=lambda _: None,
    )

    assert first == second

    # Exactly two DynamoDB tables.
    tables = aws.dynamodb.list_tables()["TableNames"]

    assert set(tables) == {
        settings.facts_table_name,
        settings.reports_table_name,
    }

    # Exactly one ALB.
    assert len(
        aws.elbv2.describe_load_balancers()["LoadBalancers"]
    ) == 1

    # Exactly two target groups.
    assert len(
        aws.elbv2.describe_target_groups()["TargetGroups"]
    ) == 2

    # Exactly two Auto Scaling Groups.
    assert len(
        aws.autoscaling.describe_auto_scaling_groups()[
            "AutoScalingGroups"
        ]
    ) == 2

    # Exactly one launch template per tier.
    templates = aws.ec2.describe_launch_templates()[
        "LaunchTemplates"
    ]

    assert len(templates) == 2

    for tier in settings.tiers:
        versions = aws.ec2.describe_launch_template_versions(
            LaunchTemplateName=tier.lt_name
        )["LaunchTemplateVersions"]

        assert len(versions) >= 1

    # Exactly one dedicated application security group.
    groups = aws.ec2.describe_security_groups(
        Filters=[
            {
                "Name": "group-name",
                "Values": [settings.app_sg_name],
            },
            {
                "Name": "vpc-id",
                "Values": [first.vpc_id],
            },
        ]
    )["SecurityGroups"]

    assert len(groups) == 1
    assert len(groups[0]["IpPermissions"]) == 1

    print("PASS: Repeated creation")
    print("PASS: No duplicate DynamoDB tables")
    print("PASS: No duplicate ALB, target groups, or ASGs")
    print("PASS: No duplicate launch templates")
    print("PASS: No duplicate application security groups")



def test_delete_removes_everything_and_restores_the_app_sg(env):
    settings, aws = env

    outputs = create_stack(
        settings, aws, iam_wait=0,
        sleep=lambda _: None,
    )

    outputs.save()

    delete_stack(
        settings, aws,
        sleep=lambda _: None,
    )

    assert aws.elbv2.describe_load_balancers()["LoadBalancers"] == []
    assert aws.elbv2.describe_target_groups()["TargetGroups"] == []
    assert aws.autoscaling.describe_auto_scaling_groups()[
        "AutoScalingGroups"
    ] == []

    for tier in settings.tiers:
        assert not any(
            t["LaunchTemplateName"] == tier.lt_name
            for t in aws.ec2.describe_launch_templates()[
                "LaunchTemplates"
            ]
        )

        assert not any(
            r["RoleName"] == tier.role_name
            for r in aws.iam.list_roles()["Roles"]
        )

        assert not any(
            p["InstanceProfileName"] == tier.profile_name
            for p in aws.iam.list_instance_profiles()[
                "InstanceProfiles"
            ]
        )

    tables = aws.dynamodb.list_tables()["TableNames"]

    assert settings.facts_table_name not in tables
    assert settings.reports_table_name not in tables

    groups = aws.ec2.describe_security_groups(
        Filters=[
            {
                "Name": "vpc-id",
                "Values": [outputs.vpc_id],
            }
        ]
    )["SecurityGroups"]

    names = {g["GroupName"] for g in groups}

    assert settings.app_sg_name not in names
    assert settings.alb_sg_name not in names

    assert not Path("stack_outputs.json").exists()
    assert not Path("stack_outputs.env").exists()

    print("PASS: Project infrastructure deleted")
    print("PASS: DynamoDB tables deleted")
    print("PASS: Project security groups deleted")
    print("PASS: Output files removed")



def test_delete_is_safe_to_run_twice(env):
    settings, aws = env

    create_stack(settings, aws, iam_wait=0)

    delete_stack(settings, aws, sleep=lambda _: None)
    delete_stack(settings, aws, sleep=lambda _: None)

    # nothing left, must not raise


def test_keep_foundation_leaves_bucket_roles_and_templates(env):
    settings, aws = env

    outputs = create_stack(
        settings, aws, iam_wait=0,
        sleep=lambda _: None,
    )

    delete_stack(
        settings, aws,
        keep_foundation=True,
        sleep=lambda _: None,
    )

    assert aws.autoscaling.describe_auto_scaling_groups()[
        "AutoScalingGroups"
    ] == []

    assert aws.elbv2.describe_load_balancers()[
        "LoadBalancers"
    ] == []

    tables = aws.dynamodb.list_tables()["TableNames"]

    assert settings.facts_table_name in tables
    assert settings.reports_table_name in tables

    for tier in settings.tiers:
        aws.iam.get_role(RoleName=tier.role_name)

        aws.iam.get_instance_profile(
            InstanceProfileName=tier.profile_name
        )

        templates = aws.ec2.describe_launch_templates(
            LaunchTemplateNames=[tier.lt_name]
        )["LaunchTemplates"]

        assert len(templates) == 1

    groups = aws.ec2.describe_security_groups(
        Filters=[
            {
                "Name": "vpc-id",
                "Values": [outputs.vpc_id],
            }
        ]
    )["SecurityGroups"]

    names = {g["GroupName"] for g in groups}

    assert settings.app_sg_name not in names
    assert settings.alb_sg_name not in names

    print("PASS: Runtime infrastructure deleted")
    print("PASS: DynamoDB tables preserved")
    print("PASS: IAM roles and profiles preserved")
    print("PASS: Launch templates preserved")



def test_delete_detaches_extra_managed_policies(env):
    settings, aws = env

    create_stack(
        settings, aws, iam_wait=0,
        sleep=lambda _: None,
    )

    role_name = settings.weather.role_name

    policy_arn = aws.iam.create_policy(
        PolicyName="MotoCleanupTestPolicy",
        PolicyDocument=__import__("json").dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": ["dynamodb:DescribeTable"],
                "Resource": "*",
            }],
        }),
    )["Policy"]["Arn"]

    aws.iam.attach_role_policy(
        RoleName=role_name,
        PolicyArn=policy_arn,
    )

    attached = aws.iam.list_attached_role_policies(
        RoleName=role_name
    )["AttachedPolicies"]

    assert any(
        p["PolicyArn"] == policy_arn
        for p in attached
    )

    delete_stack(
        settings, aws,
        sleep=lambda _: None,
    )

    assert not any(
        r["RoleName"] == role_name
        for r in aws.iam.list_roles()["Roles"]
    )

    print("PASS: Managed IAM policy detached")
    print("PASS: Weather IAM role deleted")
