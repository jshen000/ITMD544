# Weather Advisor — ITMO 544 Final Project

A two-service AWS weather-advice application deployed using Python infrastructure automation. The **Weather** service retrieves US forecast data and caches results; the **Advisor** service consumes that weather data and requests clothing guidance from Gemini, with deterministic fallback advice when Gemini is unavailable.

**Repository:** https://github.com/jshen000/ITMD544/tree/main/project/final_project  
**AWS region:** `us-east-2`  
**Demo verification date:** November 20, 2026

## Architecture

```text
Client
  |
  v
Application Load Balancer (public, two availability zones)
  |-- HTTP :8080 --> Weather target group --> Weather ASG / EC2 (:3000)
  |                                            |-- Zippopotam.us ZIP lookup
  |                                            |-- NWS points + forecast APIs
  |                                            `-- DynamoDB Facts cache
  |
  `-- HTTP :8081 --> Advisor target group --> Advisor ASG / EC2 (:3000)
                                               |-- Weather API via ALB
                                               |-- Gemini API
                                               |-- SSM Parameter Store (API key)
                                               `-- DynamoDB Reports
```

Each service has its own launch template, Auto Scaling Group, target group, IAM role, and instance profile. The EC2 application security group permits TCP port `3000` from the **ALB security group only**. Public clients use the ALB listener ports rather than direct EC2 application access.

## Project layout

| Path | Purpose |
| --- | --- |
| `create_stack.py` | Entry point to create or reconcile the AWS deployment |
| `stack/config.py` | Load and validate `.env` settings |
| `stack/runner.py` | Orchestrate resource creation and cleanup helpers |
| `stack/network.py` | VPC and security-group configuration |
| `stack/balancer.py` | ALB, listeners, and target groups |
| `stack/compute.py` | Launch templates and EC2 user data |
| `stack/boot_template.py` | EC2 application bootstrap |
| `stack/policies.py` | Per-service least-privilege IAM policies |
| `weather-app/app.py` | Weather HTTP API and DynamoDB caching |
| `advisor-app/app.py` | Advice HTTP API, Gemini integration, and fallback |
| `tests/` | Unit, simulated-AWS, integration, and chaos tests |
| `pytest.ini` | Test discovery and markers |
| `stack_outputs.json` | Generated deployment identifiers and ALB DNS |

Additional modules and scripts may be present in the current checkout. Use the repository contents as the authoritative file list.

## Requirements

- Python 3.10+ and `pip` (the verified demo used Python 3.10.12)
- AWS CLI configured with permissions appropriate to the resources being managed
- AWS account with EC2, ELBv2, Auto Scaling, IAM, DynamoDB, S3, and SSM access as required by the deployment
- A Gemini API key stored securely as configured by the project
- Network access to the weather and Gemini upstream APIs

The deployment reads project-specific settings from `.env`. **Do not commit `.env`, API keys, AWS credentials, private SSH keys, or generated secrets.** Check `stack/config.py` for the exact required setting names; do not guess configuration keys.

## Set up the local test environment

From the repository checkout:

```bash
cd /ITMD544/project/final_project
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export PYTHONPATH="$PWD/advisor-app:$PWD/weather-app:$PWD"
```

If the venv already exists, activate it rather than recreating it. The `PYTHONPATH` export is important because the application folders contain hyphens and some tests import their modules explicitly.

## Run the offline tests

```bash
cd /ITMD544/project/final_project
source .venv/bin/activate
export PYTHONPATH="$PWD/advisor-app:$PWD/weather-app:$PWD"
set -o pipefail
python -m pytest -m "not integration and not chaos" -v -ra \
  2>&1 | tee unit_test_results.txt
```

**Verified result, November 20, 2026:** `96 passed, 16 deselected` (112 collected), in **26.22 seconds**. Offline tests cover application logic, Gemini fallback, validation, caching, IAM policies, bootstrap packaging, and AWS resource logic with mocks/simulation. They do not require a live deployed stack.

## Deploy or resume the AWS application

First confirm the account and region:

```bash
aws sts get-caller-identity
aws configure get region
python create_stack.py --help
```

To deploy/reconcile using the existing project entry point, after reviewing `.env` and AWS permissions:

```bash
python create_stack.py --env .env
```

**For an already-created stack**, check ASG capacity before creating or modifying anything:

```bash
aws autoscaling describe-auto-scaling-groups --region us-east-2 \
  --auto-scaling-group-names \
    itmo-444-544-final-weather-asg \
    itmo-444-544-final-advisor-asg \
  --query 'AutoScalingGroups[].[AutoScalingGroupName,MinSize,DesiredCapacity]' \
  --output table
```

The November 20 demo resumed an existing stack whose ASGs had `DesiredCapacity=0`. These commands set **one running instance per tier** and may incur AWS charges:

```bash
aws autoscaling update-auto-scaling-group \
  --auto-scaling-group-name itmo-444-544-final-weather-asg \
  --min-size 1 --desired-capacity 1 --region us-east-2

aws autoscaling update-auto-scaling-group \
  --auto-scaling-group-name itmo-444-544-final-advisor-asg \
  --min-size 1 --desired-capacity 1 --region us-east-2
```

Wait for the instances to finish bootstrapping and for both ALB targets to become healthy. An ASG instance showing `InService` does **not** by itself mean the HTTP application is ready.

## Demo: verify endpoints and ALB health

The two verified listener ports are:

- Weather: `http://<ALB_DNS>:8080/`
- Advisor: `http://<ALB_DNS>:8081/`

Use the ALB DNS stored in `stack_outputs.json` instead of hardcoding an address that may change:

```bash
ALB=$(python -c 'import json; print(json.load(open("stack_outputs.json"))["alb_dns_name"])')
curl -i --max-time 10 "http://$ALB:8080/"
curl -i --max-time 10 "http://$ALB:8081/"
```

On November 20, both root endpoints returned **HTTP 200 OK**; the Weather response was:

```json
{"status": "ok", "service": "weather"}
```

Verify actual target health:

```bash
python - <<'PY'
import json, subprocess
with open('stack_outputs.json') as f:
    outputs = json.load(f)
for tier in ('weather', 'advisor'):
    print(f'=== {tier.upper()} ===', flush=True)
    subprocess.run([
        'aws', 'elbv2', 'describe-target-health', '--region', 'us-east-2',
        '--target-group-arn', outputs[f'{tier}_tg_arn'],
        '--query', 'TargetHealthDescriptions[].[Target.Id,TargetHealth.State]',
        '--output', 'table',
    ], check=True)
PY
```

**Observed:** Weather target `healthy`; Advisor target `healthy`. Both target groups use instance port `3000`, health-check path `/`, and expected status `200`.

## Weather and Advisor request examples

The reviewed service implementation includes these JSON routes:

```bash
curl -i "http://$ALB:8080/weather?zip=60616"
curl -i "http://$ALB:8081/advice?zip=60616"
```

The Weather tier resolves a ZIP code through Zippopotam.us, fetches NWS forecast data, and caches a DynamoDB Facts record. A repeated request within the configured cache window can return `cache=hit`. The Advisor tier calls the Weather service and attempts Gemini advice, with fallback behavior on upstream failure.

**Evidence boundary:** The November 20 live integration run verified health, invalid-input handling, AWS configuration, and network controls; it did **not** establish a successful real Gemini recommendation, email delivery, or the full date/gender/Morning/Night report workflow. Demonstrate those separately before claiming them complete.

## Run the live integration tests

Only run this after deploying and confirming healthy targets:

```bash
cd /ITMD544/project/final_project
source .venv/bin/activate
export PYTHONPATH="$PWD/advisor-app:$PWD/weather-app:$PWD"
set -o pipefail
python -m pytest -m integration -v -ra \
  2>&1 | tee integration_test_results.txt
```

**Verified result, November 20, 2026:** `14 passed, 98 deselected` in **5.91 seconds**. The checks include:

- Weather and Advisor health endpoints
- Invalid ZIP/date rejection and unknown-route 404
- DynamoDB table configuration
- Weather/Advisor IAM least privilege
- Public ALB spanning two availability zones
- Correct listener-to-target-group routing and fast health checks
- Auto Scaling recovery configuration
- Application port accessible only from the ALB
- Healthy targets in both target groups

**Total verified in these two test runs: 110 passed, 0 failed** (96 offline + 14 live integration). This total excludes chaos tests.

## Chaos and recovery testing

A separate test marker exists for intentional disruption:

```bash
python -m pytest -m chaos -v -ra
```

**Do not run this as part of a normal smoke test.** It can terminate or disrupt AWS resources. Historical evidence reported recovery times of **52.545 seconds** (Weather) and **62.413 seconds** (Advisor) against a 600-second recovery SLO. These are **earlier recorded measurements**, not results from the November 20 integration run.

## Troubleshooting notes from the live demo

Initially, the ASGs had no running instances. After resuming them, both EC2 instances became `InService` but the target groups briefly reported `Target.Timeout` / `Target.FailedHealthChecks`. Once application startup completed, both targets became `healthy` and both root endpoints returned HTTP 200. EC2 console output showed cloud-init completion and `python3-boto3` installation; that alone did not establish application readiness.

An attempt to use `aws ssm describe-instance-information` failed with `AccessDeniedException` for the lab IAM user. This was a **diagnostic permission limitation**, not evidence that the deployed applications failed. The ALB health results and integration tests were used as the final readiness checks.

## Submission evidence

Include the relevant report and evidence files with your submission:

| Evidence | Meaning |
| --- | --- |
| `unit_test_results.txt` | 96 passing offline tests |
| `integration_test_results.txt` | 14 passing live integration tests |
| `final_target_health.txt` | Both ALB targets healthy, if captured |
| `final_test_evidence.txt` | Earlier test evidence, if present |
| `chaos_test_evidence.txt` | Historical recovery evidence, if present |
| Technical report | Coding walkthrough, AWS design, testing, and demo appendix |

Review files before committing. Stage only the intended source and evidence files; do not use `git add .` without checking for secrets and backup files.

## Cost control and cleanup

After the demo, scale down the **two project ASGs** to stop their EC2 capacity:

```bash
aws autoscaling update-auto-scaling-group \
  --auto-scaling-group-name itmo-444-544-final-weather-asg \
  --region us-east-2 --min-size 0 --desired-capacity 0

aws autoscaling update-auto-scaling-group \
  --auto-scaling-group-name itmo-444-544-final-advisor-asg \
  --region us-east-2 --min-size 0 --desired-capacity 0
```

This is **partial cleanup only**. The ALB, DynamoDB, S3, IAM, and other infrastructure may remain, and some resources may continue to incur charges. Review the project-specific deletion logic in `stack/runner.py` before attempting full removal; do not delete unrelated lab resources.

## Limitations and future improvements

The reviewed implementation and verified demo do not yet prove all assignment requirements, including the complete Morning/Night report, ten required weather fields per period, and confirmation/delivery email workflow. Future work should also add request-level observability, stronger abuse controls, measured end-to-end latency, and load testing.
