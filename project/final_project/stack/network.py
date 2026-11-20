"""Default VPC, subnets, and the two security groups."""

from __future__ import annotations

import logging
import time

from .clients import AwsClients
from .config import Settings
from .util import ignore_errors, retry_on


log = logging.getLogger("stack")


class StackError(Exception):
    """Something the lab cannot continue without (for example, no subnet in an AZ)."""


def default_vpc_id(aws: AwsClients) -> str:
    vpcs = aws.ec2.describe_vpcs(
        Filters=[{"Name": "isDefault", "Values": ["true"]}]
    )["Vpcs"]

    if not vpcs:
        raise StackError("No default VPC found in this region.")

    return vpcs[0]["VpcId"]


def subnet_in_az(aws: AwsClients, vpc_id: str, az: str) -> str:
    subnets = aws.ec2.describe_subnets(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "availability-zone", "Values": [az]},
        ]
    )["Subnets"]

    if not subnets:
        raise StackError(f"The default VPC has no subnet in {az}.")

    return subnets[0]["SubnetId"]


def _cidr_rule(port: int, cidr: str = "0.0.0.0/0") -> list[dict]:
    return [
        {
            "IpProtocol": "tcp",
            "FromPort": port,
            "ToPort": port,
            "IpRanges": [{"CidrIp": cidr}],
        }
    ]


def _group_rule(port: int, group_id: str) -> list[dict]:
    return [
        {
            "IpProtocol": "tcp",
            "FromPort": port,
            "ToPort": port,
            "UserIdGroupPairs": [{"GroupId": group_id}],
        }
    ]


def find_security_group(
    aws: AwsClients,
    name: str,
    vpc_id: str,
) -> str | None:
    groups = aws.ec2.describe_security_groups(
        Filters=[
            {"Name": "group-name", "Values": [name]},
            {"Name": "vpc-id", "Values": [vpc_id]},
        ]
    )["SecurityGroups"]

    return groups[0]["GroupId"] if groups else None


def ensure_alb_security_group(
    aws: AwsClients,
    settings: Settings,
    vpc_id: str,
) -> str:
    group_id = find_security_group(aws, settings.alb_sg_name, vpc_id)

    if group_id is None:
        group_id = aws.ec2.create_security_group(
            GroupName=settings.alb_sg_name,
            Description="Allow inbound to the ALB on the app listener ports",
            VpcId=vpc_id,
        )["GroupId"]
        log.info("Created ALB security group %s", group_id)

    for tier in settings.tiers:
        with ignore_errors("InvalidPermission.Duplicate"):
            aws.ec2.authorize_security_group_ingress(
                GroupId=group_id,
                IpPermissions=_cidr_rule(tier.listener_port),
            )

    return group_id


def restrict_app_security_group(
    aws: AwsClients,
    settings: Settings,
    alb_sg_id: str,
) -> None:
    """Replace 'port 3000 open to the world' with 'port 3000 open to the ALB only'."""
    with ignore_errors("InvalidPermission.NotFound"):
        aws.ec2.revoke_security_group_ingress(
            GroupId=settings.app_sg_id,
            IpPermissions=_cidr_rule(settings.app_port),
        )

    with ignore_errors("InvalidPermission.Duplicate"):
        aws.ec2.authorize_security_group_ingress(
            GroupId=settings.app_sg_id,
            IpPermissions=_group_rule(settings.app_port, alb_sg_id),
        )


def restore_app_security_group(
    aws: AwsClients,
    settings: Settings,
    alb_sg_id: str | None,
) -> None:
    """Remove ALB access during cleanup without reopening public access."""
    if alb_sg_id:
        with ignore_errors("InvalidPermission.NotFound"):
            aws.ec2.revoke_security_group_ingress(
                GroupId=settings.app_sg_id,
                IpPermissions=_group_rule(settings.app_port, alb_sg_id),
            )

    log.info(
        "Removed ALB access rule from application security group %s",
        settings.app_sg_id,
    )



def delete_alb_security_group(
    aws: AwsClients,
    group_id: str,
    sleep=time.sleep,
) -> None:
    # The load balancer's network interfaces can linger for a minute
    # after it is deleted.
    with ignore_errors("InvalidGroup.NotFound"):
        retry_on(
            {"DependencyViolation"},
            lambda: aws.ec2.delete_security_group(GroupId=group_id),
            attempts=10,
            delay=15,
            sleep=sleep,
        )

    log.info("Deleted ALB security group %s", group_id)


def ensure_app_security_group(
    aws: AwsClients,
    settings: Settings,
    vpc_id: str,
    alb_sg_id: str,
) -> str:
    """Ensure a dedicated application group with ALB-only ingress."""
    group_id = find_security_group(
        aws,
        settings.app_sg_name,
        vpc_id,
    )

    if group_id is None:
        group_id = aws.ec2.create_security_group(
            GroupName=settings.app_sg_name,
            Description="Final project application: ALB-only ingress",
            VpcId=vpc_id,
        )["GroupId"]

    group = aws.ec2.describe_security_groups(
        GroupIds=[group_id]
    )["SecurityGroups"][0]

    for rule in group.get("IpPermissions", []):
        sources = rule.get("UserIdGroupPairs", [])

        allowed = (
            rule.get("IpProtocol") == "tcp"
            and rule.get("FromPort") == settings.app_port
            and rule.get("ToPort") == settings.app_port
            and not rule.get("IpRanges")
            and not rule.get("Ipv6Ranges")
            and not rule.get("PrefixListIds")
            and len(sources) == 1
            and sources[0].get("GroupId") == alb_sg_id
        )

        if not allowed:
            with ignore_errors("InvalidPermission.NotFound"):
                aws.ec2.revoke_security_group_ingress(
                    GroupId=group_id,
                    IpPermissions=[rule],
                )

    with ignore_errors("InvalidPermission.Duplicate"):
        aws.ec2.authorize_security_group_ingress(
            GroupId=group_id,
            IpPermissions=_group_rule(settings.app_port, alb_sg_id),
        )

    log.info("Application security group %s is ready", group_id)
    return group_id


def delete_app_security_group(
    aws: AwsClients,
    group_id: str,
    sleep=time.sleep,
) -> None:
    """Delete the dedicated app group, waiting for instance cleanup."""
    with ignore_errors("InvalidGroup.NotFound"):
        retry_on(
            {"DependencyViolation"},
            lambda: aws.ec2.delete_security_group(
                GroupId=group_id
            ),
            attempts=10,
            delay=15,
            sleep=sleep,
        )

    log.info("Application security group %s is absent", group_id)
