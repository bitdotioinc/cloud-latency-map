# cloud-latency-map
Find the latency for locations around the world from each datacenter in GCP and AWS.
Process those latencies, and create an interactive map that lets you explore the
expected latency to a datacenter from anywhere in the world.

You can see this at https://bit.io/latency


There's a blog post on this as well - https://innerjoin.bit.io/exploring-cloud-datacenter-latency-e6245278e71b
## Running

The web site, which is static, and you can literally open the
app.html file in your browswer to explore it. The geojson files checked into this repo
should be good enough.

To create/update the coverage geojson, you need to run the more interesting bits of this:
the tracer & the locator.

The tracer uses `mtr` and the `mtrpacket` libraries to send, in parallel, traceroutes to 
IPs spread throughout the world. The tracer records those results per-IP in the `probes` table.

Then, when the locator is run, the locator goes through the `probes` (joined with the `ip_location`
table to prevent re-locating IPs we already have located) and tries two methods to geolocate
the IP (the open maxmind database and the commerical ipinfo data). Once the IP is located we updated
the public database's `latency_from_datacenter` table with the latitude, longitude, and latency. 

Then one last script creates the geojson files by computing the alphashape around the points, one
alphashape for every millisecond of latency between 10 and 150 milliseconds for all regions and cloud
providers. 


### Running in production

You need a GCP account & and AWS account, two bit.io databases, and an ipinfo account.

Set the environment variables:

 * `TRACER_PRIV_DB_CONNSTR` - the bit.io (or Postgres) connect string for the _private_ database
 * `TRACER_PUBLIC_DB_CONNSTR` - the bit.io (or Postgres) connect string for the _public_ database
 * `TRACER_IPINFO_KEY` - the ipinfo key
 * `TRACER_IPINFO_BATCH_URL` - the ipinfo batch URL

Then edit `tracer/aws_json/*` and put your AWS customer ID in the right places. Do the same in `tracer/build-aws.sh`

Make sure you are logged into gcloud and aws - both those CLI tools are required.


Dump the files in `schema/` to the public & private bit.io databases.

Running `tracer/build-aws.sh` will deploy the tracer image to everywhere in AWS and run 10 tasks for each region, twice a day.
Running `tracer/build.sh` will do mostly the same for GCP.

Every so often (or as a scheduled job) run `./update_geojson.sh` 






