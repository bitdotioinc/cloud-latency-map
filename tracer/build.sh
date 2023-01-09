#!/bin/bash
set -exu -o pipefail
# SET YOUR GCP PROJECT HERE
PROJECT=


REGIONS_WITH_SCHEDULER="us-central1 europe-west1  northamerica-northeast1 us-east1 us-west1  asia-east1 asia-northeast1 asia-northeast3 asia-south1 asia-southeast1 australia-southeast1 southamerica-east1 us-west4"
REGIONS_WITHOUT_SCHEDULER="asia-east2 asia-northeast2 asia-south2 asia-southeast2 australia-southeast2 europe-central2 europe-north1 europe-southwest1 europe-west2 europe-west3 europe-west4 europe-west6 europe-west8 europe-west9 me-west1 northamerica-northeast2 southamerica-west1 us-east4 us-east5 us-south1 us-west2 us-west3"
REGIONS="${REGIONS_WITHOUT_SCHEDULER} ${REGIONS_WITH_SCHEDULER}"
JOB_NAME="tracer"

# We trace 100 ips in a job, so set TASK_COUNT to something that will cover a lot of IPs.
TASK_COUNT=10

gcloud builds submit --tag us-central1-docker.pkg.dev/${PROJECT}/latency-mapper/tracer:latest --project ${PROJECT}

minute=0
hour=8

for r in ${REGIONS}
do

  # Setup the cloud run jobs
  set +e
  gcloud beta run jobs list --project ${PROJECT} --region ${r} | grep ${JOB_NAME}-${r}
  ret=$?
  set -e
  if [ $ret -eq 1 ]; then
    gcloud beta run jobs create ${JOB_NAME}-${r} --project ${PROJECT} --max-retries=0 --tasks=${TASK_COUNT} --parallelism=${TASK_COUNT} --task-timeout=15m --image us-central1-docker.pkg.dev/${PROJECT}/latency-mapper/tracer:latest --region ${r}
  else
    gcloud beta run jobs update ${JOB_NAME}-${r} --project ${PROJECT} --max-retries=0  --task-timeout=15m --tasks=${TASK_COUNT} --parallelism=${TASK_COUNT} --image us-central1-docker.pkg.dev/${PROJECT}/latency-mapper/tracer:latest --region ${r}
  fi

  # Setup the schedule

  # run at 8:00am daily, presumably local time to the region?
  SCHEDULER_REGION=${r}
  if [[ "${REGIONS_WITHOUT_SCHEDULER}" == *"${r}"* ]]; then
    SCHEDULER_REGION='us-central1'
  fi

  set +e
  gcloud scheduler jobs list --project ${PROJECT} --location ${SCHEDULER_REGION} | grep ${JOB_NAME}-${r}-scheduled
  ret=$?
  set -e
  if [ $ret -eq 1 ]; then
    gcloud scheduler jobs create http ${JOB_NAME}-${r}-scheduled \
        --project ${PROJECT} \
        --oauth-service-account-email=tracer-job-trigger@${PROJECT}.iam.gserviceaccount.com	\
        --location ${SCHEDULER_REGION} \
        --schedule="${minute} ${hour} * * *" \
        --uri="https://${r}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT}/jobs/${JOB_NAME}-${r}:run" \
        --http-method POST 
  else
    gcloud scheduler jobs update http ${JOB_NAME}-${r}-scheduled \
        --project ${PROJECT} \
        --oauth-service-account-email=tracer-job-trigger@${PROJECT}.iam.gserviceaccount.com	\
        --location ${SCHEDULER_REGION} \
        --schedule="${minute} ${hour} * * *" \
        --uri="https://${r}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT}/jobs/${JOB_NAME}-${r}:run" \
        --http-method POST 
  fi

  ((minute=minute+5))
  if [[ "$minute" -gt 59 ]]; then
    ((hour=hour+1))
    minute=0
  fi
done

