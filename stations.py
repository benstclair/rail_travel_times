import math
import re
from pathlib import Path

import pandas as pd
import requests

RAW = Path("data/raw/naptan")
API = "https://naptan.api.dft.gov.uk/v1"
DOWNLOADS = {
    "stops.csv": f"{API}/access-nodes?dataFormat=csv",
    "stops.xml": f"{API}/access-nodes?dataFormat=xml",
    "localities.csv": f"{API}/nptg/localities?dataFormat=csv",
}
GREATER_LONDON_AREA = "082"


def download(name, url):
    path = RAW / name
    if not path.exists():
        RAW.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(path, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
    return path


def osgb_to_wgs84(easting, northing):
    a, b, f0 = 6377563.396, 6356256.909, 0.9996012717
    lat0, lon0, e0, n0 = math.radians(49), math.radians(-2), 400000, -100000
    e2 = 1 - b * b / (a * a)
    n = (a - b) / (a + b)
    lat, m = lat0, 0
    while northing - n0 - m >= 1e-5:
        lat += (northing - n0 - m) / (a * f0)
        m = b * f0 * (
            (1 + n + 1.25 * n**2 + 1.25 * n**3) * (lat - lat0)
            - (3 * n + 3 * n**2 + 2.625 * n**3) * math.sin(lat - lat0) * math.cos(lat + lat0)
            + (1.875 * n**2 + 1.875 * n**3) * math.sin(2 * (lat - lat0)) * math.cos(2 * (lat + lat0))
            - 35 / 24 * n**3 * math.sin(3 * (lat - lat0)) * math.cos(3 * (lat + lat0))
        )
    sin, cos, tan = math.sin(lat), math.cos(lat), math.tan(lat)
    nu = a * f0 / math.sqrt(1 - e2 * sin**2)
    rho = a * f0 * (1 - e2) / (1 - e2 * sin**2) ** 1.5
    eta2 = nu / rho - 1
    de = easting - e0
    lat_osgb = lat - tan / (2 * rho * nu) * de**2 + tan / (24 * rho * nu**3) * (5 + 3 * tan**2 + eta2 - 9 * tan**2 * eta2) * de**4
    lon_osgb = lon0 + de / (cos * nu) - (nu / rho + 2 * tan**2) / (6 * cos * nu**3) * de**3 + (5 + 28 * tan**2 + 24 * tan**4) / (120 * cos * nu**5) * de**5
    nu = a / math.sqrt(1 - e2 * math.sin(lat_osgb) ** 2)
    x = nu * math.cos(lat_osgb) * math.cos(lon_osgb)
    y = nu * math.cos(lat_osgb) * math.sin(lon_osgb)
    z = nu * (1 - e2) * math.sin(lat_osgb)
    s = 20.4894e-6
    rx, ry, rz = (math.radians(v / 3600) for v in (-0.1502, -0.247, -0.8421))
    x2 = -446.448 + (1 + s) * x - rz * y + ry * z
    y2 = 125.157 + rz * x + (1 + s) * y - rx * z
    z2 = -542.060 - ry * x + rx * y + (1 + s) * z
    a2, e2w = 6378137.0, 1 - 6356752.3141**2 / 6378137.0**2
    p = math.hypot(x2, y2)
    lat_w = math.atan2(z2, p * (1 - e2w))
    for _ in range(10):
        lat_w = math.atan2(z2 + e2w * a2 / math.sqrt(1 - e2w * math.sin(lat_w) ** 2) * math.sin(lat_w), p)
    return math.degrees(lat_w), math.degrees(math.atan2(y2, x2))


def rail_refs(xml_path):
    text = xml_path.read_text(encoding="utf-8-sig")
    rows = []
    for m in re.finditer(r"<StopPoint .*?</StopPoint>", text, re.S):
        block = m.group()
        if "<TiplocRef>" not in block:
            continue
        field = lambda tag: (re.search(rf"<{tag}>(.*?)</{tag}>", block) or [None, None])[1]
        rows.append({
            "atco": field("AtcoCode"),
            "tiploc": field("TiplocRef"),
            "crs": field("CrsRef"),
            "locality": field("NptgLocalityRef"),
            "easting": field("Easting"),
            "northing": field("Northing"),
            "active": 'Status="inactive"' not in block[:300],
        })
    return pd.DataFrame(rows)


paths = {name: download(name, url) for name, url in DOWNLOADS.items()}
refs = rail_refs(paths["stops.xml"])
stops = pd.read_csv(paths["stops.csv"], dtype=str, usecols=["ATCOCode", "CommonName", "Latitude", "Longitude", "Status"])
localities = pd.read_csv(paths["localities.csv"], dtype=str, usecols=["NptgLocalityCode", "AdministrativeAreaCode"])

df = (
    refs[refs.active]
    .merge(stops[stops.Status == "active"], left_on="atco", right_on="ATCOCode")
    .merge(localities, left_on="locality", right_on="NptgLocalityCode", how="left")
    .assign(
        name=lambda d: d.CommonName.str.replace(r" (Rail )?Station$", "", regex=True),
        lat=lambda d: d.Latitude.astype(float),
        lon=lambda d: d.Longitude.astype(float),
        in_london=lambda d: d.AdministrativeAreaCode == GREATER_LONDON_AREA,
    )
    .drop_duplicates("tiploc")
    .sort_values("tiploc")
)
missing = df.lat.isna()
df.loc[missing, ["lat", "lon"]] = [osgb_to_wgs84(float(e), float(n)) for e, n in zip(df.easting[missing], df.northing[missing])]
df = df[["tiploc", "crs", "name", "lat", "lon", "atco", "in_london"]]
df.to_csv("data/stations.csv", index=False)

print(len(df), "rows;", df.crs.nunique(), "distinct CRS;", df.crs.isna().sum(), "without CRS;", df.tiploc.isna().sum(), "without TIPLOC;", df.in_london.sum(), "in London")
print(df[df.name.str.contains("Liverpool Street|Farringdon|Stratford|London Bridge|Paddington")].to_string())
