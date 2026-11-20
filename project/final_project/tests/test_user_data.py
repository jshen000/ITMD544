import base64
import ast

from stack.compute import build_user_data, launch_template_data


def test_app_files_are_embedded_and_round_trip(fake_settings):
    tier = fake_settings.weather
    script = build_user_data(fake_settings, tier)

    assert script.startswith("#!/usr/bin/python3")
    assert "PAYLOAD = json.loads" in script
    assert "base64.b64decode" in script
    assert "lzma.decompress" in script
    assert "app.py" not in script or len(script) > 100


def test_service_gets_the_right_environment(fake_settings):
    weather = build_user_data(
        fake_settings,
        fake_settings.weather,
    )

    advisor = build_user_data(
        fake_settings,
        fake_settings.advisor,
    )

    assert "PAYLOAD = json.loads" in weather
    assert "PAYLOAD = json.loads" in advisor

    assert weather != advisor


def test_script_starts_with_a_shebang(fake_settings):
    script = build_user_data(
        fake_settings,
        fake_settings.weather,
    )

    assert script.startswith("#!/usr/bin/python3")
    ast.parse(script)
    compile(script, "ec2-user-data.py", "exec")


def test_launch_template_user_data_is_base64_of_the_script(
    fake_settings,
):
    tier = fake_settings.advisor

    data = launch_template_data(fake_settings, tier)

    decoded = base64.b64decode(
        data["UserData"]
    ).decode()

    assert decoded == build_user_data(fake_settings, tier)

    assert data["IamInstanceProfile"] == {
        "Name": tier.profile_name
    }

    assert data["NetworkInterfaces"][0]["Groups"] == [
        fake_settings.app_sg_id
    ]
