import asyncio
import logging
import os
from datetime import datetime
from time import gmtime
from typing import Any, Dict, Optional

import geoip2.database  # type: ignore
import ipinfo
import psycopg
import requests  # type: ignore
from geopy import distance
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


# Your API key to IPinfo
ipinfo_token = os.getenv("TRACER_IPINFO_KEY", None)
assert ipinfo_token


ip_info_batch_url = os.getenv("TRACER_IPINFO_BATCH_URL", "None")
assert ip_info_batch_url

# Your private bit.io DB
connstr = os.getenv(
    "TRACER_PRIV_DB_CONNSTR",
    (None),
)
assert connstr

public_connstr = os.getenv(
    "TRACER_PUBLIC_DB_CONNSTR",
    (None),
)

assert public_connstr


def get_source_location() -> tuple[str, str]:

    source_location = "bitio-sanfran1"
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


def get_ips_to_locate() -> Dict[str, Dict[str, Any]]:
    provider, source_location = get_source_location()
    with psycopg.connect(conninfo=connstr) as conn:
        with conn.cursor() as cur:
            # Filtering out the 172 RFC1918 is too much of a
            # pain in the ass to do in sql :)
            s = sql.SQL(
                """
                SELECT ip, latency_ms, provider, source_name
                FROM   probes p
                WHERE has_bad_location = 'F' and NOT EXISTS (
                SELECT  FROM   ip_location_data
                    WHERE  ip = p.ip
                    ) AND ip NOT LIKE '10.%' AND ip NOT LIKE '192.168.%' limit 10000;
            """
            )
            cur.execute(s)
            ips_to_locate = cur.fetchall()
    probed_ips = {}
    for row in ips_to_locate:
        probed_ips[row[0]] = {
            "latency_ms": row[1],
            "provider": row[2],
            "region": row[3],
            "ip": row[0],
        }
    num_ips = len(probed_ips.keys())
    logging.info(f"Got {num_ips} to locate")
    return probed_ips


def get_dc_locations() -> Dict[tuple[Any, Any], tuple[Any, Any]]:
    with psycopg.connect(conninfo=connstr) as conn:
        with conn.cursor() as cur:
            s = sql.SQL("select distinct provider, region, lat, lon from dc_locations ")
            cur.execute(s)
            dc_locs = cur.fetchall()
    dc_locations = {}
    for loc in dc_locs:
        dc_locations[(loc[0], loc[1])] = (loc[2], loc[3])
    return dc_locations


class NonsenseDistanceException(Exception):
    pass


class LocationDataRecord(object):
    def __init__(
        self,
        ip: str,
        city: str,
        country: str,
        lat: float,
        lon: float,
        as_info: str,
        isp: str,
        latency_ms: float,
        is_confirmed: bool,
        dc_lat: float,
        dc_lon: float,
        region: str,
        provider: str,
    ):
        self.ip = ip
        self.city = city
        self.country = country
        self.lat = lat
        self.lon = lon
        self.as_info = as_info
        self.isp = isp
        self.latency_ms = latency_ms
        self.dc_lat = dc_lat
        self.dc_lon = dc_lon
        self.is_confirmed = is_confirmed
        self.region = region
        self.provider = provider

        self.distance_in_km = distance.distance((dc_lat, dc_lon), (lat, lon)).km

    def check_distance(self) -> None:
        # I had a formula derived from real-world pings from WonderNetwork,
        # regression done on distance & latency from a 250+
        # sites from 10 different sources.
        # But all I really needed was:
        if (self.distance_in_km > 0) and (
            self.latency_ms / self.distance_in_km < 0.0095
        ):
            logging.info(
                f"{self.ip}: got a distance ({self.distance_in_km} km) and latency "
                f"({self.latency_ms} ms) that don't make sense."
            )
            raise NonsenseDistanceException("Distance doesn't make sense!")


async def insert_location_data(
    ldr: LocationDataRecord,
    provider: str,
    source_location: str,
    conn: Connection,
    cur: Cursor,
    pub_conn: Connection,
    pub_cur: Cursor,
) -> None:
    s = sql.SQL(
        """
INSERT INTO
    ip_location_data (ip, city, country, lat, lon, as_info,
                      is_location_confirmed, last_check_time, isp)
VALUES
    (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (ip)
DO UPDATE SET city=EXCLUDED.city,
    country=EXCLUDED.country, lat=EXCLUDED.lat,
    lon=EXCLUDED.lon, isp=EXCLUDED.isp,
    as_info=EXCLUDED.as_info,
    last_check_time=EXCLUDED.last_check_time;
    """
    )
    await cur.execute(
        s,
        (
            ldr.ip,
            ldr.city,
            ldr.country,
            ldr.lat,
            ldr.lon,
            ldr.as_info,
            ldr.is_confirmed,
            datetime.utcnow(),
            ldr.isp,
        ),
    )
    await conn.commit()

    s = sql.SQL(
        """
INSERT INTO
    latency_from_datacenter (provider, region, geodesic_distance_km,
                            last_update_utc, source_lat, source_lon,
                            dest_lat, dest_lon, latency_ms)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (provider, region, dest_lat, dest_lon)
DO UPDATE SET geodesic_distance_km=EXCLUDED.geodesic_distance_km,
                last_update_utc=EXCLUDED.last_update_utc,
                source_lat=EXCLUDED.source_lat,
                source_lon=EXCLUDED.source_lon, dest_lat=EXCLUDED.dest_lat,
                dest_lon=EXCLUDED.dest_lon, latency_ms=EXCLUDED.latency_ms;
    """
    )
    await pub_cur.execute(
        s,
        (
            provider,
            source_location,
            ldr.distance_in_km,
            datetime.utcnow(),
            ldr.dc_lat,
            ldr.dc_lon,
            ldr.lat,
            ldr.lon,
            ldr.latency_ms,
        ),
    )
    await pub_conn.commit()
    logging.info(
        f"Added location for {ldr.ip}, distance from {source_location} is "
        f"{ldr.distance_in_km} km with a latency of {ldr.latency_ms} ms"
    )


async def locate_ips(
    probed_ips: Dict[Any, Any]
) -> tuple[Optional[Dict[Any, Any]], Optional[Dict[Any, Any]]]:
    logging.info("adding locations")

    if probed_ips == {}:
        logging.info("no locations to lookup, returning")
        return None, None

    ips_with_lat_lon = {}
    ips_with_bad_lat_lon = {}
    possible_ips_to_locate = list(probed_ips.keys())

    values = (
        "(values " + ", ".join(["('" + x + "')" for x in possible_ips_to_locate]) + ")"
    )
    ips_to_locate = []

    q = (
        f"SELECT DISTINCT ips.ip FROM {values} as ips(ip) "
        "LEFT OUTER JOIN ip_location_data ON "
        "ips.ip = ip_location_data.ip "
        "WHERE ip_location_data.ip IS NULL;"
    )

    with psycopg.connect(conninfo=connstr) as conn:
        with conn.cursor() as cur:
            cur.execute(q)
            ips_to_locate = [x[0] for x in cur.fetchall()]

    pips_count = len(possible_ips_to_locate)
    ips_count = len(ips_to_locate)
    logging.info(
        f"Initial set of {pips_count} ips reduced to {ips_count} "
        "after checking if we have located the ips before."
    )
    dc_locations = get_dc_locations()

    async with await psycopg.AsyncConnection.connect(conninfo=connstr) as conn:
        async with conn.cursor() as cur:
            async with await psycopg.AsyncConnection.connect(
                conninfo=public_connstr
            ) as pub_conn:
                async with pub_conn.cursor() as pub_cur:
                    with geoip2.database.Reader("mmdb/GeoLite2-City.mmdb") as gip:
                        for ip in ips_to_locate:
                            provider = probed_ips[ip]["provider"]
                            source_location = probed_ips[ip]["region"]
                            latency_ms = probed_ips[ip]["latency_ms"]
                            dc_lat, dc_lon = dc_locations[(provider, source_location)]
                            try:
                                answer = gip.city(ip)

                                as_info = ""
                                isp = ""
                                is_confirmed = False
                                ldr = LocationDataRecord(
                                    ip,
                                    answer.city.name,
                                    answer.country.name,
                                    answer.location.latitude,
                                    answer.location.longitude,
                                    as_info,
                                    isp,
                                    latency_ms,
                                    is_confirmed,
                                    dc_lat,
                                    dc_lon,
                                    provider,
                                    source_location,
                                )
                                ldr.check_distance()
                                await insert_location_data(
                                    ldr,
                                    provider,
                                    source_location,
                                    conn,
                                    cur,
                                    pub_conn,
                                    pub_cur,
                                )
                                ips_with_lat_lon[ip] = {
                                    "ip": ip,
                                    "lat": ldr.lat,
                                    "lon": ldr.lon,
                                    "latency_ms": ldr.latency_ms,
                                    "distance_in_km": ldr.distance_in_km,
                                    "provider": provider,
                                    "region": source_location,
                                }
                            except (
                                NonsenseDistanceException,
                                geoip2.errors.AddressNotFoundError,
                            ):
                                ips_with_bad_lat_lon[ip] = {
                                    "ip": ip,
                                    "provider": provider,
                                    "region": source_location,
                                    "latency_ms": latency_ms,
                                }

    return ips_with_lat_lon, ips_with_bad_lat_lon


async def update_probes_with_bad_location_flag(probed_ips: Dict[Any, Any]) -> None:
    ips_to_locate = list(probed_ips.keys())
    values = "(" + ", ".join(["'" + x + "'" for x in ips_to_locate]) + ")"
    async with await psycopg.AsyncConnection.connect(conninfo=connstr) as conn:
        async with conn.cursor() as cur:
            s = sql.SQL(
                f"UPDATE probes SET has_bad_location = TRUE where ip IN {values}"
            )
            await cur.execute(s)


async def relocate_ips_with_service(
    probed_ips: Dict[Any, Any]
) -> tuple[Optional[Dict[Any, Any]], Optional[Dict[Any, Any]]]:
    # for any ip whose distance + latency didn't make sense,
    # relocate with the commercial ipinfo service
    if probed_ips == {}:
        logging.info("No locations to lookup, returning")
        return None, None

    max_ips_in_batch = 900
    ipinfo_handler = ipinfo.getHandler(ipinfo_token)

    dc_locations = get_dc_locations()

    ips_with_lat_lon = {}
    ips_with_bad_lat_lon = {}
    ips_to_locate = list(probed_ips.keys())
    count_of_ips_to_locate = len(ips_to_locate)
    logging.info(f"Relocating {count_of_ips_to_locate}")

    async with await psycopg.AsyncConnection.connect(conninfo=connstr) as conn:
        async with conn.cursor() as cur:
            async with await psycopg.AsyncConnection.connect(
                conninfo=public_connstr
            ) as pub_conn:
                async with pub_conn.cursor() as pub_cur:
                    while ips_to_locate:
                        ips = ips_to_locate[:max_ips_in_batch]
                        del ips_to_locate[:max_ips_in_batch]

                        answers = ipinfo_handler.getBatchDetails(ips)
                        for answer in answers.values():
                            try:
                                ip = answer["ip"]
                                if answer.get("bogon", False):
                                    logging.info(f"bogon ip: {ip}")
                                    ips_with_bad_lat_lon[ip] = probed_ips[ip]
                                    continue
                                as_info = ""
                                isp = ""
                                if "org" in answer:
                                    as_info = answer["org"].split(" ")[0]
                                    isp = answer["org"]

                                latency_ms = probed_ips[ip]["latency_ms"]
                                provider = probed_ips[ip]["provider"]
                                source_location = probed_ips[ip]["region"]
                                dc_lat, dc_lon = dc_locations[
                                    (provider, source_location)
                                ]

                                is_confirmed = True
                                ldr = LocationDataRecord(
                                    ip,
                                    answer["city"],
                                    answer["country_name"],
                                    answer["latitude"],
                                    answer["longitude"],
                                    as_info,
                                    isp,
                                    latency_ms,
                                    is_confirmed,
                                    dc_lat,
                                    dc_lon,
                                    provider,
                                    source_location,
                                )
                                ldr.check_distance()
                                await insert_location_data(
                                    ldr,
                                    provider,
                                    source_location,
                                    conn,
                                    cur,
                                    pub_conn,
                                    pub_cur,
                                )

                                ips_with_lat_lon[ip] = {
                                    "ip": ldr.ip,
                                    "lat": ldr.lat,
                                    "lon": ldr.lon,
                                    "latency_ms": probed_ips[ip]["latency_ms"],
                                    "distance_in_km": ldr.distance_in_km,
                                }
                            except NonsenseDistanceException:
                                ips_with_bad_lat_lon[ip] = {
                                    "ip": ldr.ip,
                                    "lat": ldr.lat,
                                    "lon": ldr.lon,
                                    "latency_ms": probed_ips[ip]["latency_ms"],
                                    "distance_in_km": ldr.distance_in_km,
                                }

                            except Exception as e:
                                logging.info(e)
                                logging.exception(e)
    return ips_with_lat_lon, ips_with_bad_lat_lon


async def main() -> None:
    ips = get_ips_to_locate()

    ips_with_lat_lon, ips_with_bad_lat_lon = await locate_ips(ips)

    if ips_with_bad_lat_lon:
        logging.info("I've got possible bad ips, " "I'll see what we can do.")
        ips_with_lat_lon, ips_with_bad_lat_lon = await relocate_ips_with_service(
            ips_with_bad_lat_lon
        )

    if ips_with_bad_lat_lon:
        await update_probes_with_bad_location_flag(ips_with_bad_lat_lon)


asyncio.run(main())
