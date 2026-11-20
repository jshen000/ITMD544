"""Does the stack you deployed look the way the design says it should?

Run after create_stack.py:
    pytest -m integration
"""

import pytest

from tests.helpers import wait_until

pytestmark = pytest.mark.integration


def test_dynamodb_tables_are_configured(aws, settings):
    for name in (
        settings.facts_table_name,
        settings.reports_table_name,
    ):
        table = aws.dynamodb.describe_table(
            TableName=name
        )["Table"]

        assert table["KeySchema"] == [
            {"AttributeName": "pk", "KeyType": "HASH"}
        ]

        assert table["BillingModeSummary"]["BillingMode"] == (
            "PAY_PER_REQUEST"
        )

        ttl = aws.dynamodb.describe_time_to_live(
            TableName=name
        )["TimeToLiveDescription"]

        assert ttl["TimeToLiveStatus"] in (
            "ENABLED",
            "ENABLING",
        )
        assert ttl["AttributeName"] == "expires"



def _decision(aws, role_name, action, resource):
    role_arn = aws.iam.get_role(RoleName=role_name)["Role"]["Arn"]

    result = aws.iam.simulate_principal_policy(
        PolicySourceArn=role_arn,
        ActionNames=[action],
        ResourceArns=[resource],
    )

    return result["EvaluationResults"][0]["EvalDecision"]


def test_weather_role_has_least_privilege(aws, settings):
    role = settings.weather.role_name

    facts_arn = aws.dynamodb.describe_table(
        TableName=settings.facts_table_name
    )["Table"]["TableArn"]

    reports_arn = aws.dynamodb.describe_table(
        TableName=settings.reports_table_name
    )["Table"]["TableArn"]

    assert _decision(
        aws, role, "dynamodb:GetItem", facts_arn
    ) == "allowed"

    assert _decision(
        aws, role, "dynamodb:PutItem", facts_arn
    ) == "allowed"

    assert _decision(
        aws, role, "dynamodb:DeleteItem", facts_arn
    ) != "allowed"

    assert _decision(
        aws, role, "dynamodb:GetItem", reports_arn
    ) != "allowed"



def test_advisor_role_has_least_privilege(aws, settings):
    role = settings.advisor.role_name

    reports_arn = aws.dynamodb.describe_table(
        TableName=settings.reports_table_name
    )["Table"]["TableArn"]

    facts_arn = aws.dynamodb.describe_table(
        TableName=settings.facts_table_name
    )["Table"]["TableArn"]

    identity = aws.sts.get_caller_identity()
    partition = identity["Arn"].split(":")[1]

    parameter_arn = (
        f"arn:{partition}:ssm:{settings.region}:"
        f"{identity['Account']}:parameter/"
        f"{settings.gemini_param_name.lstrip('/')}"
    )

    assert _decision(
        aws, role, "dynamodb:GetItem", reports_arn
    ) == "allowed"

    assert _decision(
        aws, role, "dynamodb:PutItem", reports_arn
    ) == "allowed"

    assert _decision(
        aws, role, "dynamodb:GetItem", facts_arn
    ) != "allowed"

    assert _decision(
        aws, role, "ssm:GetParameter", parameter_arn
    ) == "allowed"

    assert _decision(
        aws, role, "ssm:PutParameter", parameter_arn
    ) != "allowed"



def test_load_balancer_is_public_and_spans_two_azs(aws, settings, outputs):
    lb = aws.elbv2.describe_load_balancers(
        LoadBalancerArns=[outputs.alb_arn]
    )["LoadBalancers"][0]

    assert lb["State"]["Code"] == "active"
    assert lb["Scheme"] == "internet-facing"

    zones = {az["ZoneName"] for az in lb["AvailabilityZones"]}

    assert zones == {settings.az_a, settings.az_b}


def test_listeners_forward_to_the_right_target_groups(aws, settings, outputs):
    listeners = aws.elbv2.describe_listeners(
        LoadBalancerArn=outputs.alb_arn
    )["Listeners"]

    forwards = {
        item["Port"]: item["DefaultActions"][0]["TargetGroupArn"]
        for item in listeners
    }

    for tier in settings.tiers:
        assert forwards[tier.listener_port] == outputs.tg_arn(tier.name)


def test_target_groups_use_fast_health_checks(aws, settings, outputs):
    for tier in settings.tiers:
        tg = aws.elbv2.describe_target_groups(
            TargetGroupArns=[outputs.tg_arn(tier.name)]
        )["TargetGroups"][0]

        assert tg["HealthCheckPath"] == "/"
        assert tg["HealthCheckIntervalSeconds"] == 10
        assert tg["HealthyThresholdCount"] == 2


def test_auto_scaling_groups_are_configured_for_recovery(aws, settings, outputs):
    for tier in settings.tiers:
        group = aws.autoscaling.describe_auto_scaling_groups(
            AutoScalingGroupNames=[tier.asg_name]
        )["AutoScalingGroups"][0]

        assert group["HealthCheckType"] == "ELB"
        assert group["DesiredCapacity"] == 1
        assert set(group["VPCZoneIdentifier"].split(",")) == {
            outputs.subnet_a,
            outputs.subnet_b,
        }
        assert outputs.tg_arn(tier.name) in group["TargetGroupARNs"]


def test_app_port_is_reachable_only_from_the_load_balancer(
    aws, settings, outputs
):
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

    assert not rule.get("IpRanges")
    assert not rule.get("Ipv6Ranges")
    assert not rule.get("PrefixListIds")

    assert [
        item["GroupId"]
        for item in rule["UserIdGroupPairs"]
    ] == [outputs.alb_security_group_id]



def test_every_target_becomes_healthy(aws, settings, outputs):
    def all_healthy():
        for tier in settings.tiers:
            targets = aws.elbv2.describe_target_health(
                TargetGroupArn=outputs.tg_arn(tier.name)
            )["TargetHealthDescriptions"]

            if not targets or any(
                t["TargetHealth"]["State"] != "healthy" for t in targets
            ):
                return False

        return True

    assert wait_until(
        all_healthy, timeout=420, interval=15
    ), "targets never became healthy"
