#!/bin/bash
set -exu -o pipefail
# SET YOUR AWS CUSTOMER ID HERE
AWS_CUSTOMER_ID=


REGIONS="af-south-1 ap-east-1 ap-northeast-1 ap-northeast-2 ap-northeast-3 ap-south-1 ap-southeast-1 ap-southeast-2 ca-central-1 eu-central-1 eu-north-1 eu-south-1 eu-west-1 eu-west-2 eu-west-3 me-south-1 sa-east-1 us-east-1 us-east-2 us-west-1 us-west-2"
DOCKER_REGISTRY=${AWS_CUSTOMER_ID}.dkr.ecr.us-west-2.amazonaws.com/tracer-images

# In AWS, the max task count is 10
TASK_COUNT=10

ECR_PASSWORD=$(aws ecr get-login-password --region us-west-2)
echo "${ECR_PASSWORD}" | docker login -u AWS --password-stdin ${DOCKER_REGISTRY}
docker build -t tracer:latest .
docker tag tracer:latest ${DOCKER_REGISTRY}
docker push  ${DOCKER_REGISTRY}

minute=0
hour=8
hour2=14

set +e
aws iam list-attached-role-policies --role-name ecsTaskExecutionRole | grep CloudWatchLogsFullAccess
ret=$?
set -e
if [ $ret -eq 1 ]; then
    aws iam attach-role-policy --role-name ecsTaskExecutionRole --policy-arn arn:aws:iam::aws:policy/CloudWatchLogsFullAccess
fi

set +e
aws iam create-policy --policy-name tracerEventBridgeRunTaskPolicy --policy-document file://./aws_json/policy.json
aws iam create-role --role-name tracerEventBridgeRunTaskRole --assume-role-policy-document file://./aws_json/trust_policy.json
POLICY_ARN=$(aws iam list-policies --query 'Policies[?PolicyName==\`tracerEventBridgeRunTaskPolicy\`]' --output text | cut -f1 | grep arn)
aws iam attach-role-policy --role-name tracerEventBridgeRunTaskRole --policy-arn "${POLICY_ARN}"
set -e

for r in ${REGIONS}
do
    CLUSTER_NAME=tracer-cluster-${r}
    CONFIG_NAME=tracer-config-${r}
    set +e
    aws ecs describe-clusters --region ${r} --cluster ${CLUSTER_NAME} --query clusters[*].clusterName --output text | grep ${CLUSTER_NAME}
    ret=$?
    set -e
    if [ $ret -eq 1 ]; then
        ecs-cli configure --cluster ${CLUSTER_NAME} --region ${r} --default-launch-type FARGATE --config-name ${CONFIG_NAME}
        ecs-cli up --region ${r} --cluster ${CLUSTER_NAME}
    fi

    set +e
    aws ecs describe-task-definition --region ${r} --task-definition tracer --query taskDefinition.containerDefinitions[0].name
    ret=$?
    set -e
    if [ $ret -eq 254 ]; then
        aws ecs register-task-definition  --no-cli-pager  --region ${r} --cli-input-json "$(jq --arg r "${r}" '.containerDefinitions[0].logConfiguration.options."awslogs-region" = $r' aws_json/task_definition.json)"
    fi

    VPC=`aws ec2 describe-subnets --region ${r}  --query 'Subnets[*].[VpcId]' --filter Name=tag:aws:cloudformation:stack-name,Values=amazon-ecs-cli-setup-tracer* --output text | uniq`
    read -a SUBNETS < <(aws ec2 describe-subnets --region ${r}  --query 'Subnets[*].SubnetId' --filter Name=tag:aws:cloudformation:stack-name,Values=amazon-ecs-cli-setup-tracer* --output text)
    SECURITY_GROUP=`aws ec2 describe-security-groups --region ${r} --filters Name=vpc-id,Values=${VPC} --query 'SecurityGroups[?GroupName==\`default\`].GroupId' --output text`
    TASK_ARN=`aws ecs describe-task-definition --region ${r}  --task-definition tracer --query taskDefinition.taskDefinitionArn --output text`
    EVENT_BRIDGE_INVOKER_ROLE_ARN=`aws iam get-role --role-name tracerEventBridgeRunTaskRole --output text  | grep arn | cut -f2`
    CLUSTER_ARN=`aws ecs --region ${r} list-clusters --output text | grep ${CLUSTER_NAME} | cut -f2`
    SCHEDULER_NAME=tracer-scheduled-${r}

    aws events --region ${r} put-rule --name tracerSchedule-${r} --schedule-expression "cron(${minute} ${hour},${hour2} * * ? *)"
    aws events --region ${r} put-targets --rule tracerSchedule-${r} \
        --cli-input-json "$(cat aws_json/event_bridge_target.json | sed -e 's/subnet_1/'"${SUBNETS[0]}"'/' -e 's/subnet_2/'"${SUBNETS[1]}"'/' \
        -e 's/_name/'"${SCHEDULER_NAME}"'/' -e 's|event_bridge_role_arn|'"${EVENT_BRIDGE_INVOKER_ROLE_ARN}"'|' -e 's|task_definition_arn|'"${TASK_ARN}"'|'  \
        -e 's|cluster_arn|'"${CLUSTER_ARN}"'|' -e 's/security_group/'"${SECURITY_GROUP}"'/' -e 's/_task_count/'"${TASK_COUNT}"'/')"

((minute=minute+5))
if [[ "$minute" -gt 59 ]]; then
    ((hour=hour+1))
    ((hour2=hour2+1))
    minute=0
fi
done
