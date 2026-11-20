"""The two entry points: build the whole stack, and tear it down."""

from __future__ import annotations

import logging
import time

from . import artifacts, balancer, compute, network, scaling, storage
from .clients import AwsClients
from .config import Settings
from .outputs import StackOutputs


log = logging.getLogger("stack")


def create_stack(
    settings: Settings,
    aws: AwsClients,
    iam_wait: int = 15,
    sleep=time.sleep,
) -> StackOutputs:
    log.info("== S3 application artifacts ==")
    storage.ensure_bucket(aws, settings)
    tier_artifacts = artifacts.upload_tier_artifacts(
        aws.s3,
        settings.bucket_name,
        settings.tiers,
    )

    log.info("== DynamoDB tables ==")

    facts_table_arn = storage.ensure_table(
        aws,
        settings.facts_table_name,
    )
    reports_table_arn = storage.ensure_table(
        aws,
        settings.reports_table_name,
    )

    identity = aws.sts.get_caller_identity()
    account_id = identity["Account"]
    partition = identity["Arn"].split(":")[1]
    parameter_name = settings.gemini_param_name.lstrip("/")

    gemini_parameter_arn = (
        f"arn:{partition}:ssm:{settings.region}:{account_id}"
        f":parameter/{parameter_name}"
    )

    log.info("== IAM roles and instance profiles ==")

    for tier in settings.tiers:
        storage.ensure_role_and_profile(
            aws,
            settings,
            tier,
            facts_table_arn=facts_table_arn,
            reports_table_arn=reports_table_arn,
            gemini_parameter_arn=gemini_parameter_arn,
        )

    log.info("Waiting %ss for IAM propagation...", iam_wait)
    sleep(iam_wait)

    from dataclasses import replace

    log.info("== Network ==")
    vpc_id = network.default_vpc_id(aws)
    subnet_a = network.subnet_in_az(aws, vpc_id, settings.az_a)
    subnet_b = network.subnet_in_az(aws, vpc_id, settings.az_b)

    alb_sg_id = network.ensure_alb_security_group(
        aws, settings, vpc_id
    )

    app_sg_id = network.ensure_app_security_group(
        aws, settings, vpc_id, alb_sg_id
    )

    settings = replace(settings, app_sg_id=app_sg_id)

    log.info("== Target groups, load balancer, listeners ==")
    tg_arns = {
        tier.name: balancer.ensure_target_group(aws, settings, tier, vpc_id)
        for tier in settings.tiers
    }
    alb_arn, alb_dns = balancer.ensure_load_balancer(
        aws,
        settings,
        [subnet_a, subnet_b],
        alb_sg_id,
    )

    for tier in settings.tiers:
        balancer.ensure_listener(
            aws,
            alb_arn,
            tier.listener_port,
            tg_arns[tier.name],
        )

    weather_url = (
        f"http://{alb_dns}:{settings.weather.listener_port}"
    )

    log.info("== Launch templates ==")
    for tier in settings.tiers:
        compute.ensure_launch_template(
            aws, settings, tier,
            weather_url=weather_url if tier.name == "advisor" else None,
            artifact=tier_artifacts[tier.name],
        )

    log.info("== Auto Scaling Groups ==")
    for tier in settings.tiers:
        scaling.ensure_asg(
            aws,
            tier,
            tg_arns[tier.name],
            [subnet_a, subnet_b],
        )

    return StackOutputs(
        vpc_id=vpc_id,
        subnet_a=subnet_a,
        subnet_b=subnet_b,
        alb_security_group_id=alb_sg_id,
        alb_arn=alb_arn,
        alb_dns_name=alb_dns,
        weather_tg_arn=tg_arns["weather"],
        advisor_tg_arn=tg_arns["advisor"],
    )


def delete_stack(
    settings: Settings,
    aws: AwsClients,
    keep_foundation: bool = False,
    sleep=time.sleep,
) -> None:
    """Delete by name, not by saved state, so it still works after a half-finished create."""
    log.info("== Auto Scaling Groups (this also terminates their instances) ==")
    for tier in settings.tiers:
        scaling.delete_asg(aws, tier.asg_name, sleep=sleep)

    log.info("== Load balancer and target groups ==")
    balancer.delete_load_balancer(aws, settings.alb_name)

    for tier in settings.tiers:
        balancer.delete_target_group(aws, tier.tg_name)

    log.info("== Security groups ==")
    vpc_id = network.default_vpc_id(aws)

    app_sg_id = network.find_security_group(
        aws,
        settings.app_sg_name,
        vpc_id,
    )
    alb_sg_id = network.find_security_group(
        aws,
        settings.alb_sg_name,
        vpc_id,
    )

    if app_sg_id:
        network.delete_app_security_group(
            aws, app_sg_id, sleep=sleep
        )

    if alb_sg_id:
        network.delete_alb_security_group(
            aws, alb_sg_id, sleep=sleep
        )

    if keep_foundation:
        log.info(
            "Keeping DynamoDB tables, IAM roles and launch templates "
            "(--keep-foundation)."
        )
    else:
        log.info("== Launch templates, IAM, DynamoDB tables ==")

        for tier in settings.tiers:
            compute.delete_launch_template(aws, tier)
            storage.delete_role_and_profile(aws, tier)

        storage.delete_table(aws, settings.reports_table_name)
        storage.delete_table(aws, settings.facts_table_name)

    StackOutputs.remove()
    log.info("Cleanup complete.")
