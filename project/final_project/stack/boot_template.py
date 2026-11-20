#!/usr/bin/python3
"""Prepare application files using Python only."""

import base64
import json
from pathlib import Path


PAYLOAD = None  # Filled by build_user_data.


def prepare_files(payload):
    """Write packaged application files and non-secret settings."""
    root = Path("/opt/weather-advisor")
    root.mkdir(parents=True, exist_ok=True)

    for filename, encoded in payload["files"].items():
        relative = Path(filename)

        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe application filename")

        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(
            base64.b64decode(encoded, validate=True)
        )

    config = root / "config.json"
    config.write_text(
        json.dumps(payload["config"], indent=2) + "\n"
    )
    config.chmod(0o600)

    return root


def supervise_application(root):
    """Run the Python application without shell commands."""
    import fcntl
    import os
    import pwd
    import sys
    import time

    entry = root / "app.py"
    if not entry.is_file():
        raise FileNotFoundError("Application app.py is missing")

    user = pwd.getpwnam("ec2-user")

    # The configuration contains settings, never secret values.
    config = root / "config.json"
    os.chown(config, user.pw_uid, user.pw_gid)
    config.chmod(0o600)

    lock = (root / "supervisor.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return

    if os.fork():
        return

    os.setsid()
    os.chdir(root)

    with open("/dev/null", "rb") as source:
        os.dup2(source.fileno(), 0)

    with open("/var/log/weather-advisor.log", "ab", buffering=0) as log:
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)

    while True:
        child = os.fork()

        if child == 0:
            os.setgroups([])
            os.setgid(user.pw_gid)
            os.setuid(user.pw_uid)

            environment = dict(os.environ)
            environment["HOME"] = user.pw_dir
            environment["APP_CONFIG"] = str(config)
            environment["PYTHONPATH"] = str(root / "vendor")

            os.execve(
                sys.executable,
                [sys.executable, str(entry)],
                environment,
            )

        os.waitpid(child, 0)
        time.sleep(2)



def install_dependencies():
    """Install AWS SDK dependencies on Amazon Linux 2023."""
    import importlib.util
    import subprocess

    if importlib.util.find_spec("boto3") is not None:
        return

    subprocess.run(
        ["dnf", "install", "-y", "python3-boto3"],
        check=True,
        timeout=300,
    )


def main():
    """Prepare the application and start it on initial boot or reboot."""
    if not isinstance(PAYLOAD, dict):
        raise RuntimeError("Deployment payload has not been supplied")

    install_dependencies()
    if "artifact" in PAYLOAD:
        import boto3

        artifact = PAYLOAD["artifact"]
        root = Path("/opt/weather-advisor")

        install_artifact(
            boto3.client("s3", region_name=PAYLOAD["config"]["AWS_REGION"]),
            artifact["bucket"],
            artifact["key"],
            artifact["sha256"],
            root,
        )

        config = root / "config.json"
        config.write_text(
            json.dumps(PAYLOAD["config"], indent=2) + '\n'
        )
        config.chmod(0o600)
    else:
        root = prepare_files(PAYLOAD)

    hook = Path(
        "/var/lib/cloud/scripts/per-boot/weather-advisor.py"
    )
    hook.parent.mkdir(parents=True, exist_ok=True)

    source = Path(__file__)
    if source.resolve() != hook.resolve():
        hook.write_text(source.read_text())
        hook.chmod(0o700)

    supervise_application(root)


if __name__ == "__main__":
    main()
