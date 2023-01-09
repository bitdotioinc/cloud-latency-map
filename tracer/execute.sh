#!/bin/bash
set -exu -o pipefail
REGIONS_WITH_SCHEDULER="us-central1 europe-west1  northamerica-northeast1 us-east1 us-west1  asia-east1 asia-northeast1 asia-northeast3 asia-south1 asia-southeast1 australia-southeast1 southamerica-east1 us-west4"
REGIONS_WITHOUT_SCHEDULER="asia-east2 asia-northeast2 asia-south2 asia-southeast2 australia-southeast2 europe-central2 europe-north1 europe-southwest1 europe-west2 europe-west3 europe-west4 europe-west6 europe-west8 europe-west9 me-west1 northamerica-northeast2 southamerica-west1 us-east4 us-east5 us-south1 us-west2 us-west3"
REGIONS="${REGIONS_WITHOUT_SCHEDULER} ${REGIONS_WITH_SCHEDULER}"


# SET YOUR GCP PROJECT HERE
PROJECT=
JOB_NAME="tracer"

for r in ${REGIONS}
do
  gcloud beta run jobs execute tracer-${r} --project ${PROJECT} --region ${r} &
done

