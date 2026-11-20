#!/usr/bin/env bash
# ITMD544 final-project AWS cleanup and verification.
# Run from /ITMD544/project/final_project. Default mode is read-only.
set -Eeuo pipefail

REGION="${AWS_REGION:-us-east-2}"
PREFIX="itmo-444-544-final"
MODE="${1:---check}"
ENV_FILE="${ENV_FILE:-.env}"
LOG="${CLEANUP_LOG:-final_cleanup_$(date +%Y%m%d_%H%M%S).log}"

if [[ "$MODE" != "--check" && "$MODE" != "--destroy" ]]; then
  echo "Usage: $0 [--check|--destroy]" >&2; exit 2
fi
command -v aws >/dev/null || { echo 'AWS CLI required'; exit 2; }
command -v python >/dev/null || { echo 'Python required'; exit 2; }
export AWS_DEFAULT_REGION="$REGION"
export ENV_FILE
exec > >(tee -a "$LOG") 2>&1

echo "Region: $REGION | Project prefix: $PREFIX | Mode: $MODE"
aws sts get-caller-identity --query '{Account:Account,Arn:Arn}' --output json

if [[ "$MODE" == "--destroy" ]]; then
  [[ -f "$ENV_FILE" ]] || { echo "Missing $ENV_FILE; refusing deletion"; exit 2; }
  [[ -f previous/delete_stack.py ]] || { echo 'Missing previous/delete_stack.py; refusing deletion'; exit 2; }
  # Prevent using another lab's configuration. Check names before deleting.
  python - <<'PY'
from stack.config import Settings
import os
s=Settings.from_file(os.environ.get('ENV_FILE','.env'))
prefix='itmo-444-544-final'
names=[s.alb_name,s.app_sg_name,s.alb_sg_name,s.reports_table_name,s.facts_table_name]
for tier in s.tiers:
    names.extend([tier.asg_name,tier.tg_name])
    for attr in ('lt_name','launch_template_name'):
        value=getattr(tier,attr,None)
        if value: names.append(value)
wrong=[n for n in names if not str(n).startswith(prefix)]
if wrong:
    raise SystemExit('REFUSING: config includes names outside final project: '+repr(wrong))
print('Configuration names verified as final-project scoped')
PY
  echo 'DESTRUCTIVE ACTION: final-project infrastructure will be removed.'
  read -r -p "Type DELETE ${PREFIX} to continue: " confirmation
  [[ "$confirmation" == "DELETE ${PREFIX}" ]] || { echo 'Cancelled'; exit 2; }

  # ALB deletion is asynchronous. Retry after waiting for the known ALB to disappear.
  export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
  for attempt in 1 2 3; do
    echo "Cleanup attempt $attempt"
    if python previous/delete_stack.py --env "$ENV_FILE"; then
      echo 'Project cleanup entrypoint completed.'
      break
    fi
    echo 'Cleanup returned an error; waiting for ALB deletion before retry.'
    alb_arn=$(aws elbv2 describe-load-balancers --region "$REGION" \
      --query "LoadBalancers[?LoadBalancerName=='${PREFIX}-alb'].LoadBalancerArn | [0]" \
      --output text 2>/dev/null || true)
    if [[ -n "$alb_arn" && "$alb_arn" != None ]]; then
      aws elbv2 wait load-balancers-deleted --region "$REGION" --load-balancer-arns "$alb_arn" || true
    else
      sleep 15
    fi
    if [[ "$attempt" == 3 ]]; then
      echo 'Cleanup did not complete after retries; see errors and verification below.'
    fi
  done
fi

# Read-only inventory; intentionally does not silently delete S3/SSM/logs shared with other labs.
echo '=== FINAL-PROJECT INVENTORY / VERIFICATION ==='
python - <<'PY'
import boto3, os, json
from botocore.exceptions import ClientError
r=os.environ.get('AWS_DEFAULT_REGION','us-east-2')
p='itmo-444-544-final'
s=boto3.Session(region_name=r)
findings=[]; unknown=[]
def check(label, fn):
    try:
        names=sorted(set(str(x) for x in fn() if x))
        print(f'{label}: '+(json.dumps(names) if names else 'NONE'))
        if names: findings.append((label,names))
    except Exception as e:
        print(f'{label}: UNVERIFIED ({type(e).__name__}: {e})')
        unknown.append(label)
def pages(client,op,key,**kwargs):
    pg=client.get_paginator(op)
    for page in pg.paginate(**kwargs):
        yield from page.get(key,[])
a=s.client('autoscaling'); elb=s.client('elbv2'); ec2=s.client('ec2')
iam=s.client('iam'); ddb=s.client('dynamodb'); s3=s.client('s3'); ssm=s.client('ssm'); logs=s.client('logs')
check('ASGs',lambda:(x['AutoScalingGroupName'] for x in pages(a,'describe_auto_scaling_groups','AutoScalingGroups') if x['AutoScalingGroupName'].startswith(p)))
check('Load balancers',lambda:(x['LoadBalancerName'] for x in pages(elb,'describe_load_balancers','LoadBalancers') if x['LoadBalancerName'].startswith(p)))
check('Target groups',lambda:(x['TargetGroupName'] for x in pages(elb,'describe_target_groups','TargetGroups') if x['TargetGroupName'].startswith(p)))
check('Launch templates',lambda:(x['LaunchTemplateName'] for x in pages(ec2,'describe_launch_templates','LaunchTemplates') if x['LaunchTemplateName'].startswith(p)))
check('Security groups',lambda:(x['GroupId']+' '+x['GroupName'] for x in pages(ec2,'describe_security_groups','SecurityGroups') if x['GroupName'].startswith(p)))
check('DynamoDB tables',lambda:(x for x in pages(ddb,'list_tables','TableNames') if x.startswith(p)))
check('IAM roles',lambda:(x['RoleName'] for x in pages(iam,'list_roles','Roles') if x['RoleName'].startswith(p)))
check('IAM instance profiles',lambda:(x['InstanceProfileName'] for x in pages(iam,'list_instance_profiles','InstanceProfiles') if x['InstanceProfileName'].startswith(p)))
check('IAM customer policies',lambda:(x['PolicyName'] for x in pages(iam,'list_policies','Policies',Scope='Local') if x['PolicyName'].startswith(p)))
check('S3 buckets (candidate leftovers)',lambda:(x['Name'] for x in s3.list_buckets()['Buckets'] if x['Name'].startswith(p)))
check('SSM parameters (candidate leftovers)',lambda:(x['Name'] for x in pages(ssm,'describe_parameters','Parameters') if x['Name'].startswith('/'+p) or x['Name'].startswith(p)))
check('CloudWatch log groups (candidate leftovers)',lambda:(x['logGroupName'] for x in pages(logs,'describe_log_groups','logGroups') if p in x['logGroupName']))
# Tag-based EC2 check avoids treating all lab instances as final-project resources.
def instances():
    for res in pages(ec2,'describe_instances','Reservations',Filters=[{'Name':'instance-state-name','Values':['pending','running','stopping','stopped','shutting-down']} ]):
        for x in res.get('Instances',[]):
            tags=x.get('Tags',[])
            if any(p in str(t.get('Value','')) for t in tags):
                yield x['InstanceId']+' '+x['State']['Name']
check('Tagged EC2 instances',instances)
print('\nRESULT:')
if findings:
    print('REMAINING OR CANDIDATE RESOURCES: review items above. S3/SSM/log groups require ownership review before deletion.')
elif unknown:
    print('NO RESOURCES FOUND IN SUCCESSFUL CHECKS, but some checks could not be verified.')
else:
    print('NO MATCHING RESOURCES FOUND by the checks above.')
if unknown:
    print('UNVERIFIED CHECKS: '+', '.join(unknown))
PY

echo "Log saved to: $LOG"
