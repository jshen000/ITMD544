"""Settings for the stack, read from the same .env file the bash scripts use."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


REQUIRED_KEYS = [
    "AWS_REGION",
    "AMI_ID",
    "KEY_NAME",
    "INSTANCE_TYPE",
    "APP_SECURITY_GROUP_ID",
    "APP_SG_NAME",
    "WEATHER_ROLE_NAME",
    "WEATHER_PROFILE_NAME",
    "WEATHER_LT_NAME",
    "WEATHER_TG_NAME",
    "WEATHER_ASG_NAME",
    "WEATHER_LISTENER_PORT",
    "ADVISOR_ROLE_NAME",
    "ADVISOR_PROFILE_NAME",
    "ADVISOR_LT_NAME",
    "ADVISOR_TG_NAME",
    "ADVISOR_ASG_NAME",
    "ADVISOR_LISTENER_PORT",
    "ALB_NAME",
    "ALB_SG_NAME",
    "AZ_A",
    "AZ_B",
    "RECOVERY_SLO_SECONDS",
    "FACTS_TABLE_NAME",
    "REPORTS_TABLE_NAME",
    "ARTIFACT_BUCKET_NAME",
    "GEMINI_PARAM_NAME",
]


class ConfigError(Exception):
    """Raised when .env is missing keys, with a message that says which."""


def load_dotenv(path: str | Path = ".env") -> dict[str, str]:
    """Read KEY=VALUE lines into a dict. Comments and blank lines are skipped."""
    values: dict[str, str] = {}

    for raw in Path(path).read_text().splitlines():
        line = raw.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")

    return values


@dataclass(frozen=True)
class Tier:
    """Everything that belongs to one application tier (weather or advisor)."""

    name: str
    role_name: str
    profile_name: str
    lt_name: str
    tg_name: str
    asg_name: str
    listener_port: int
    app_dir: Path

    @property
    def policy_name(self) -> str:
        return f"{self.role_name}-policy"

    @property
    def service_name(self) -> str:
        return self.name


@dataclass(frozen=True)
class Settings:
    region: str
    ami_id: str
    key_name: str
    instance_type: str
    app_sg_id: str
    app_sg_name: str
    alb_name: str
    alb_sg_name: str
    az_a: str
    az_b: str
    recovery_slo_seconds: int
    facts_table_name: str
    reports_table_name: str
    bucket_name: str
    gemini_param_name: str
    weather: Tier
    advisor: Tier
    app_port: int = 3000

    @property
    def tiers(self) -> tuple[Tier, Tier]:
        return (self.weather, self.advisor)

    def tier(self, name: str) -> Tier:
        return {"weather": self.weather, "advisor": self.advisor}[name]

    @classmethod
    def from_values(
        cls,
        v: dict[str, str],
        base_dir: str | Path = ".",
    ) -> "Settings":
        missing = [k for k in REQUIRED_KEYS if not v.get(k)]

        if missing:
            raise ConfigError("Missing keys in .env: " + ", ".join(missing))

        base = Path(base_dir)

        def tier(prefix: str) -> Tier:
            return Tier(
                name=prefix.lower(),
                role_name=v[f"{prefix}_ROLE_NAME"],
                profile_name=v[f"{prefix}_PROFILE_NAME"],
                lt_name=v[f"{prefix}_LT_NAME"],
                tg_name=v[f"{prefix}_TG_NAME"],
                asg_name=v[f"{prefix}_ASG_NAME"],
                listener_port=int(v[f"{prefix}_LISTENER_PORT"]),
                app_dir=base / f"{prefix.lower()}-app",
            )

        return cls(
            region=v["AWS_REGION"],
            ami_id=v["AMI_ID"],
            key_name=v["KEY_NAME"],
            instance_type=v["INSTANCE_TYPE"],
            app_sg_id=v["APP_SECURITY_GROUP_ID"],
            app_sg_name=v["APP_SG_NAME"],
            alb_name=v["ALB_NAME"],
            alb_sg_name=v["ALB_SG_NAME"],
            az_a=v["AZ_A"],
            az_b=v["AZ_B"],
            recovery_slo_seconds=int(v["RECOVERY_SLO_SECONDS"]),
            facts_table_name=v["FACTS_TABLE_NAME"],
            reports_table_name=v["REPORTS_TABLE_NAME"],
            bucket_name=v["ARTIFACT_BUCKET_NAME"],
            gemini_param_name=v["GEMINI_PARAM_NAME"],
            weather=tier("WEATHER"),
            advisor=tier("ADVISOR"),
        )

    @classmethod
    def from_file(cls, path: str | Path = ".env") -> "Settings":
        path = Path(path)
        return cls.from_values(
            load_dotenv(path),
            base_dir=path.resolve().parent,
        )
