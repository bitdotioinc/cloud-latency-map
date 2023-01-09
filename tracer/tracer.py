import asyncio
import ipaddress
import logging
import os
import random
import re
import uuid
from datetime import datetime
from statistics import median
from time import gmtime
from typing import Any, Dict

import mtrpacket
import psycopg
import requests  # type: ignore
import utils
from psycopg import Connection, Cursor, sql

# https://stackoverflow.com/a/7517430/49489
logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s.%(msecs)03dZ %(module)s %(filename)s"
        ":%(lineno)d %(funcName)s %(message)s"
    ),
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logging.Formatter.converter = gmtime


# Your bit.io credentials for the _private_ database
# This database has IPs->lat/lon mappings in it, which we don't
# want to be public.
connstr = os.getenv(
    "TRACER_PRIV_DB_CONNSTR",
    (None),
)

assert connstr
# Your bit.io credentials for the _public_ database
public_connstr = os.getenv(
    "TRACER_PUBLIC_DB_CONNSTR",
    (None),
)
assert public_connstr


def get_source_location() -> tuple[str, str]:

    source_location = "local_machine"
    provider = "dev"
    if os.getenv("CLOUD_RUN_JOB", None):
        res = requests.get(
            "http://metadata.google.internal/computeMetadata/v1/instance/region",
            headers={"Metadata-Flavor": "Google"},
        )
        source_location = res.text.split("/")[-1]
        provider = "gcp"
    elif os.getenv("AWS_REGION", None):
        source_location = os.getenv("AWS_REGION", "")
        provider = "aws"
    if os.getenv("AZURE_LOCATION", None):
        source_location = os.getenv("AZURE_LOCATION", "")
        provider = "azure"
    return provider, source_location


# From
# github.com/matt-kimball/mtr-packet-python/blob/master/examples/trace-concurrent.py
class ProbeRecord:
    def __init__(self, ttl: int):
        self.ttl = ttl
        self.success = False
        self.ip_addrs: list[str] = []
        self.probe_times: list[float] = []
        self.mtr_result = ""


#  Perform multiple probes with a specific time to live (TTL) value
async def probe_ttl(
    provider: str,
    my_ip: str,
    mtr: mtrpacket.MtrPacket,
    hostname: str,
    ttl: int,
    record: ProbeRecord,
) -> None:
    for count in range(3):
        if provider == "azure":
            result = await mtr.probe(
                hostname, ttl=ttl, timeout=1, protocol="udp", port="33434"
            )
        else:
            result = await mtr.probe(hostname, ttl=ttl, timeout=1)

        record.mtr_result = result.result
        if result.success:
            record.success = True
        else:
            if result.result not in ["ttl-expired", "no-reply"]:
                logging.info(f"Trace {my_ip}->{hostname} failed: {result.result}")

        #  Record the time of the latest probe
        record.probe_times.append(result.time_ms)

        addr = result.responder
        #  If the address of the responder isn't already in the list
        #  of addresses responding at this TTL, add it
        if addr and addr not in record.ip_addrs:
            record.ip_addrs.append(addr)

        #  Wait a small amount of time before sending the next probe
        #  to get an independent sample of network conditions
        await asyncio.sleep(0.05)


#  Launch all the probes for the trace.
#  We'll use a separate coroutine (probe_ttl) for each ttl value,
#  and those coroutines will run concurrently.
async def launch_probes(
    my_ip: str, provider: str, hostname: str
) -> tuple[str, list[ProbeRecord]]:
    logging.info(f"From {my_ip}, starting to probe {hostname}")
    all_records = []

    async with mtrpacket.MtrPacket() as mtr:
        probe_tasks = []

        try:
            for ttl in range(1, 32):
                #  We need a new ProbeRecord for each ttl value
                record = ProbeRecord(ttl)
                all_records.append(record)

                #  Start a new asyncio task for this probe
                probe_coro = probe_ttl(provider, my_ip, mtr, hostname, ttl, record)
                probe_tasks.append(asyncio.ensure_future(probe_coro))

                #  Give each probe a slight delay to avoid flooding
                #  the network interface, which might perturb the
                #  results
                await asyncio.sleep(0.05)

            await asyncio.gather(*probe_tasks)
        except Exception as e:
            logging.info(
                f"Got exception probing from {my_ip} to target {hostname}: {e}"
            )
        finally:
            #  We may have been cancelled, so we should cancel
            #  the probe tasks we started to clean up
            for task in probe_tasks:
                task.cancel()

    logging.info(f"finished probing from {my_ip} to {hostname}")
    return (hostname, all_records)


def get_ips() -> list[Any]:
    provider, source_location = get_source_location()
    with psycopg.connect(conninfo=connstr) as conn:
        with conn.cursor() as cur:
            # TODO: limit the subclause to up to a month ago so we
            # do rescan IPs we've seen every month
            s = sql.SQL(
                "select start_ip, end_ip from ip_ranges_by_city "
                "tablesample bernoulli(1)"
            )
            cur.execute(s)
            ip_ranges = cur.fetchall()

    ips = []
    for ip_range in ip_ranges:
        ip_int = random.randint(
            int(ipaddress.IPv4Address(ip_range[0])),
            int(ipaddress.IPv4Address(ip_range[1])),
        )
        ips.append(str(ipaddress.IPv4Address(ip_int)))

    return ips


async def execute_all_probes(
    my_ip: str, hostname: str, probes: list[Any], conn: Connection, cur: Cursor
) -> Dict[Any, Any]:
    provider, source_location = get_source_location()
    ips_to_locate = {}
    seen_ips = []
    run_id = uuid.uuid4()
    for probe in probes:
        if probe.ip_addrs != [] and None not in probe.probe_times:
            logging.info(
                f"Source {my_ip}, target {hostname}: "
                f"probed {probe.ip_addrs[0]} successfully"
            )
            median_probe_time = median(probe.probe_times)
            for ip_addr in probe.ip_addrs:
                if ip_addr not in seen_ips:
                    logging.info(
                        f"Target {hostname}: Haven't seen {ip_addr} "
                        "so inserting into the probe database..."
                    )
                    try:
                        s = sql.SQL(
                            """
                            INSERT INTO probes (source_name, provider, ip, latency_ms,
                            probe_time_utc, run_id) VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (ip, source_name, provider)
                            DO UPDATE SET latency_ms=EXCLUDED.latency_ms,
                            probe_time_utc=EXCLUDED.probe_time_utc,
                            run_id=EXCLUDED.run_id
                        """
                        )
                        await cur.execute(
                            s,
                            (
                                source_location,
                                provider,
                                ip_addr,
                                median_probe_time,
                                datetime.utcnow(),
                                run_id,
                            ),
                        )
                        await conn.commit()
                        seen_ips.append(ip_addr)
                        ips_to_locate[ip_addr] = median_probe_time
                    except Exception as e:
                        logging.info(e)

    return ips_to_locate


async def get_results(provider: str, my_ip: str, all_ips: list[str]) -> Dict[Any, Any]:
    max_trace_concurrency = 50
    provider, source_location = get_source_location()
    ips_to_locate = {}
    logging.info(f"My region in '{provider}' is '{source_location}'")
    async with await psycopg.AsyncConnection.connect(conninfo=connstr) as conn:
        while all_ips:
            ips = all_ips[:max_trace_concurrency]
            del all_ips[:max_trace_concurrency]
            async with conn.cursor() as cur:
                records = await utils.gather_with_concurrency(
                    max_trace_concurrency,
                    *[launch_probes(provider, my_ip, ip) for ip in ips],
                )
                for record in records:
                    hostname = record[0]
                    probes = record[1]
                    ips_to_locate.update(
                        await execute_all_probes(my_ip, hostname, probes, conn, cur)
                    )
    return ips_to_locate


async def main() -> None:
    IPS_TO_TRACE = 75
    logging.info("getting my IP")
    res = requests.get("http://checkip.dyndns.org")
    my_ip_match = re.match(r".*: (\d+\.\d+\.\d+\.\d+).*", res.text)
    if not my_ip_match or not my_ip_match[1]:
        raise Exception("Couldn't get my IP!")
    assert my_ip_match[1]
    my_ip: str = str(my_ip_match[1])
    logging.info(f"done getting my IP: {my_ip}")
    provider, source_location = get_source_location()
    if provider == "dev":
        IPS_TO_TRACE = 2
    ips = get_ips()
    random.shuffle(ips)
    logging.info(f"Tracing {IPS_TO_TRACE} ips.")
    probed_ips = await get_results(provider, my_ip, ips[:IPS_TO_TRACE])
    new_ip_count = len(probed_ips.keys())
    logging.info(f"Finished with {new_ip_count} probed IPs.")


asyncio.run(main())
