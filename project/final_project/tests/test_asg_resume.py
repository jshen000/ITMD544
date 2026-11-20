from types import SimpleNamespace
from unittest.mock import MagicMock

from stack.scaling import ensure_asg


def make_fixture():
    client = MagicMock()
    client.describe_auto_scaling_groups.return_value = {
        "AutoScalingGroups": [{
            "AutoScalingGroupName": "test-weather-asg",
            "DesiredCapacity": 0,
        }]
    }

    aws = SimpleNamespace(autoscaling=client)
    tier = SimpleNamespace(
        asg_name="test-weather-asg",
        lt_name="test-weather-lt",
        name="weather",
    )
    return aws, tier, client


def test_existing_asg_preserves_zero_capacity():
    aws, tier, client = make_fixture()

    ensure_asg(
        aws,
        tier,
        "arn:aws:elasticloadbalancing:us-east-2:123456789012:"
        "targetgroup/test-weather/1234567890abcdef",
        ["subnet-a", "subnet-b"],
        resume=False,
    )

    client.update_auto_scaling_group.assert_not_called()
    client.create_auto_scaling_group.assert_not_called()


def test_existing_asg_resumes_to_one():
    aws, tier, client = make_fixture()

    ensure_asg(
        aws,
        tier,
        "arn:aws:elasticloadbalancing:us-east-2:123456789012:"
        "targetgroup/test-weather/1234567890abcdef",
        ["subnet-a", "subnet-b"],
        resume=True,
    )

    client.update_auto_scaling_group.assert_called_once()

    args = client.update_auto_scaling_group.call_args.kwargs
    assert args["DesiredCapacity"] == 1
    assert args["MinSize"] == 1
    assert args["MaxSize"] == 1
    assert args["LaunchTemplate"]["Version"] == "$Latest"
