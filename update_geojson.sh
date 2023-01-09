#!/bin/bash
set -exu -o pipefail
cd tracer
./update_maxmind_db.sh
python locator.py
cd ..
python create_geojson.py
cd web
gsutil cp -r . gs://bitdotio-photo-uploads-prod/latency
cd ..
