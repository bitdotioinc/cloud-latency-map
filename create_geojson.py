import logging
from multiprocessing import Pool
from time import gmtime
from typing import Any, Dict, Optional

import alphashape
import pandas as pd
import psycopg
from geopandas import GeoDataFrame, points_from_xy

# https://stackoverflow.com/a/7517430/49489
logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s.%(msecs)03dZ %(module)s "
        "%(filename)s:%(lineno)d %(funcName)s %(message)s"
    ),
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logging.Formatter.converter = gmtime


def get_dataframes(provider: str) -> tuple[GeoDataFrame, GeoDataFrame]:
    connstr = (
        "postgresql://adam:v2_3xhKN_9x8haLFy3hdLcNTqycFiWxH@"
        "db.bit.io/adam/cloud_latency_map?sslmode=require"
    )
    regions = {}
    dc_data: Dict[str, list[Any]] = {"region": [], "lat": [], "lon": [], "location": []}
    with psycopg.connect(conninfo=connstr) as conn:
        with conn.cursor() as cur:
            cur.execute(
                (
                    "select distinct region, location, lat, lon "
                    "from dc_locations where "
                    f"provider='{provider}' "
                )
            )
            dcs = cur.fetchall()
            for dc in dcs:
                r = dc[0]
                location_name = dc[1]
                lat = dc[2]
                lon = dc[3]
                dc_data["region"].append(r)
                dc_data["lat"].append(lat)
                dc_data["lon"].append(lon)
                dc_data["location"].append(location_name)
                regions[r] = {
                    "region": r,
                    "lat": lat,
                    "lon": lon,
                    "location": location_name,
                    "ips": {
                        "lat": [],
                        "lon": [],
                        "latency_ms": [],
                        "region": [],
                        "distance_in_km": [],
                    },
                }
                regions[r]["ips"]["region"].append(r)
                regions[r]["ips"]["lat"].append(lat)
                regions[r]["ips"]["lon"].append(lon)
                regions[r]["ips"]["latency_ms"].append(0)
                regions[r]["ips"]["distance_in_km"].append(0)

                cur.execute(
                    f"""select dest_lat, dest_lon, latency_ms,
                        region, geodesic_distance_km,
                        source_lat, source_lon from latency_from_datacenter
                        where region='{r}' AND provider='{provider}'
                    """
                )
                ips_for_region = cur.fetchall()

                for ip in ips_for_region:
                    lat = ip[0]
                    lon = ip[1]
                    latency_ms = ip[2]
                    r = ip[3]
                    distance_in_km = ip[4]
                    regions[r]["ips"]["lat"].append(lat)
                    regions[r]["ips"]["lon"].append(lon)
                    regions[r]["ips"]["latency_ms"].append(latency_ms)
                    regions[r]["ips"]["region"].append(r)
                    regions[r]["ips"]["distance_in_km"].append(distance_in_km)

    region_dfs = {}
    for r in regions.keys():
        if regions[r]["ips"]["lat"]:
            region_dfs[r] = pd.DataFrame(data=regions[r]["ips"])
            region_dfs[r].crs = {"init": "epsg:4326"}

        dc_df = pd.DataFrame(data=dc_data)
        dc_df.crs = {"init": "epsg:4326"}

    latency_dfs = {}
    for r in region_dfs.keys():
        geometry = points_from_xy(region_dfs[r]["lon"], region_dfs[r]["lat"])
        latency_dfs[r] = GeoDataFrame(region_dfs[r], geometry=geometry)
        latency_dfs[r].crs = {"init": "epsg:4326"}
        latency_dfs[r] = latency_dfs[r].to_crs(4326)

    geo = points_from_xy(dc_df["lon"], dc_df["lat"])
    dcs_gdf = GeoDataFrame(dc_df, geometry=geo)
    dcs_gdf.crs = {"init": "epsg:4326"}
    dcs_gdf = dcs_gdf.to_crs(4326)

    return (latency_dfs, dcs_gdf)


def get_empty_gdf() -> GeoDataFrame:
    alpha_shapes: Dict[str, Any] = {}
    alpha_shapes["data"] = {}
    alpha_shapes["data"]["region"] = []
    alpha_shapes["data"]["latency_ms"] = []
    alpha_shapes["geometry"] = []
    alpha_gdf = GeoDataFrame(
        data=alpha_shapes["data"], geometry=alpha_shapes["geometry"]
    )
    alpha_gdf.crs = {"init": "epsg:4326"}
    return alpha_gdf


def get_alpha_for_latency(
    latency_df: GeoDataFrame, region: str, latency: float, crs: str
) -> Optional[GeoDataFrame]:
    if latency % 10 == 0:
        logging.info(f"{region}: {latency}")
    try:
        under_50 = latency_df[(latency_df.latency_ms <= latency)]
        a = alphashape.alphashape(under_50, 0.1)
        s = GeoDataFrame(
            data={"region": [region], "latency_ms": [latency]}, geometry=[a.geometry[0]]
        )
        s.crs = crs
        return s
    except Exception:
        pass
    return None


def get_alphas_for_region(
    latency_df: GeoDataFrame, region: str, max_latency: int
) -> GeoDataFrame:
    logging.info(f"Starting alphashape computation for {region}...")
    alpha_gdf = get_empty_gdf()

    with Pool(5) as p:
        gdfs = p.starmap(
            get_alpha_for_latency,
            [
                *zip(
                    [latency_df] * max_latency,
                    [region] * max_latency,
                    [x for x in range(10, max_latency + 1, 1)],
                    [alpha_gdf.crs] * max_latency,
                )
            ],
        )

        alpha_gdf = pd.concat([alpha_gdf, *list(gdfs)])

    logging.info(f"Finished alphashape computation for {region}.")
    return alpha_gdf


def get_alphashapes_gdf(latency_dfs: Dict[str, Any], max_latency: int) -> GeoDataFrame:
    alpha_gdf = get_empty_gdf()
    # This is all CPU bound. We could make it faster with multiprocessing,
    # but asyncio and threads aren't going to help.
    for r in latency_dfs.keys():
        alpha_gdf = pd.concat(
            [alpha_gdf, get_alphas_for_region(latency_dfs[r], r, max_latency)]
        )
    return alpha_gdf


def dump_files(provider: str, alpha_gdf: GeoDataFrame, dcs_gdf: GeoDataFrame) -> None:
    alpha_json = alpha_gdf.to_json()
    alpha_json = f"coverageGeoJSONs['{provider}'] = " + alpha_json + ";"
    dcs_json = dcs_gdf.to_json()
    dcs_json = f"dcsLocations['{provider}'] = " + dcs_json + ";"
    with open(f"web/geojson/{provider}_coverage.js", "w") as outfile:
        outfile.write(alpha_json)

    with open(f"web/geojson/{provider}_dcs.js", "w") as outfile:
        outfile.write(dcs_json)


if __name__ == "__main__":
    max_latency = 150
    for provider in ["gcp", "aws", "azure"]:
        logging.info(f"Getting geojson for {provider}")
        latency_dfs, dcs_gdf = get_dataframes(provider)
        alpha_gdf = get_alphashapes_gdf(latency_dfs, max_latency)
        dump_files(provider, alpha_gdf, dcs_gdf)
