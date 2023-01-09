#!/bin/bash
set -exu -o pipefail
set +e
rm -rf GeoLite2-City*
rm mmdb/*
set -e
curl -o geolite2-cities.tar.gz "https://download.maxmind.com/app/geoip_download?edition_id=GeoLite2-City&license_key=zkf8WHicMmbE4yt7&suffix=tar.gz"
tar xzvf geolite2-cities.tar.gz
cp GeoLite2-City_*/GeoLite2-City.mmdb mmdb/
rm -rf GeoLite2-City*
rm geolite2-cities.tar.gz
