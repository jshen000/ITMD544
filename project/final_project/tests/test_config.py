import pytest

from stack.config import ConfigError, Settings, load_dotenv
from tests.conftest import FAKE_VALUES


def test_load_dotenv_skips_comments_and_blank_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n\nAWS_REGION=us-east-2\nKEY_NAME='quoted'\n"
    )

    assert load_dotenv(env) == {
        "AWS_REGION": "us-east-2",
        "KEY_NAME": "quoted",
    }


def test_missing_keys_are_named_in_the_error():
    values = dict(FAKE_VALUES)
    del values["ALB_NAME"]
    del values["AZ_B"]

    with pytest.raises(ConfigError) as err:
        Settings.from_values(values)

    assert "ALB_NAME" in str(err.value)
    assert "AZ_B" in str(err.value)


def test_tiers_are_built_from_prefixed_keys(fake_settings):
    weather, advisor = fake_settings.tiers

    assert weather.name == "weather"
    assert advisor.name == "advisor"

    assert weather.listener_port == int(
        FAKE_VALUES["WEATHER_LISTENER_PORT"]
    )
    assert advisor.listener_port == int(
        FAKE_VALUES["ADVISOR_LISTENER_PORT"]
    )

    assert weather.asg_name == FAKE_VALUES["WEATHER_ASG_NAME"]
    assert advisor.lt_name == FAKE_VALUES["ADVISOR_LT_NAME"]

    assert weather.policy_name == weather.role_name + "-policy"
    assert weather.app_dir.name == "weather-app"
    assert advisor.app_dir.name == "advisor-app"


def test_ports_are_integers(fake_settings):
    assert isinstance(fake_settings.weather.listener_port, int)
    assert isinstance(fake_settings.advisor.listener_port, int)
    assert isinstance(fake_settings.recovery_slo_seconds, int)
