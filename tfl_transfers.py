import argparse
import json
import time
from pathlib import Path

import pandas as pd
import requests

from router import DESTINATIONS, HUBS

URL = "https://api.tfl.gov.uk/Journey/JourneyResults/{}/to/{}"
# no Elizabeth line: the router takes its trains from the timetable
PARAMS = {"date": "20261110", "time": "0900", "timeIs": "Arriving", "journeyPreference": "LeastTime",
          "mode": "tube,dlr,overground,walking"}
ALLOWED_MODES = set(PARAMS["mode"].split(","))
OLD_MODES = ALLOWED_MODES | {"elizabeth-line"}  # modes of the original cached queries
ARRIVE_BY = "09:00:00"  # time of day, so cached responses for earlier query dates still filter correctly
CACHE = Path("data/raw/tfl_transfer_modes")  # Liverpool Street; other destinations use CACHE_<CRS>
HUB_CACHE = Path("data/raw/tfl_hub_transfers")
# split codes TfL resolves to one station (404 "No journey found"): linked as a 0-min walk, like PAD/PDX
SAME_STATION = [{"STP", "SPL"}, {"LST", "LSX"}, {"ZFD", "FDX"}]
THROTTLE_S = 0.5
MAX_TRIES = 8


def fetch(from_id, to_id):
    for attempt in range(MAX_TRIES):
        time.sleep(THROTTLE_S)
        try:
            r = requests.get(URL.format(from_id, to_id), params=PARAMS, timeout=60)
        except requests.RequestException:
            r = None
        if r is not None and r.status_code != 429 and r.status_code < 500:
            return r.json()
        time.sleep(min(2**attempt * 2, 120))
    raise RuntimeError(f"giving up on {from_id} -> {to_id}")


def search_ids(name):
    r = requests.get("https://api.tfl.gov.uk/StopPoint/Search", params={"query": name, "modes": "tube,dlr,overground,elizabeth-line"}, timeout=60)
    ids = []
    for match in r.json().get("matches", [])[:3] if r.ok else []:
        stop = requests.get(f"https://api.tfl.gov.uk/StopPoint/{match['id']}", timeout=60).json()
        ids += [match["id"]] + [child["id"] for child in stop.get("children", [])]
    return ids


def best_journey(response, modes=ALLOWED_MODES):
    journeys = [
        j for j in response.get("journeys", [])
        if j["arrivalDateTime"][11:] <= ARRIVE_BY and all(leg["mode"]["id"] in modes for leg in j["legs"])
    ]
    return min(journeys, key=lambda j: j["duration"], default=None)


def summarise(journey):
    parts = []
    for leg in journey["legs"]:
        part = "walk" if leg["mode"]["id"] == "walking" else leg["instruction"]["summary"]
        if part != "walk" or not parts or parts[-1] != "walk":
            parts.append(part)
    return ", ".join(parts)


def cached_journey(path, name, atcos, to_id):
    # an original (Elizabeth line allowed) response stands unless its fastest journey used the Elizabeth line;
    # those are re-queried without it into the parallel _no_el folder
    if path.exists():
        old = best_journey(json.loads(path.read_text()), OLD_MODES)
        if old is None or all(leg["mode"]["id"] in ALLOWED_MODES for leg in old["legs"]):
            return old
    path = path.parent.with_name(path.parent.name + "_no_el") / path.name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        ids = ["910G" + atco[4:] for atco in atcos]
        for i, station_id in enumerate(ids):
            response = fetch(station_id, to_id)
            if best_journey(response):
                break
            if i == len(ids) - 1:
                ids += [x for x in search_ids(name) if x not in ids]
        path.write_text(json.dumps(response))
    return best_journey(json.loads(path.read_text()))


def main():
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dest", choices=DESTINATIONS, help="final leg from every London station to this destination")
    mode.add_argument("--hubs", action="store_true", help="mid-journey transfers between every pair of HUBS")
    args = ap.parse_args()

    stations = pd.read_csv("data/stations.csv")
    london = stations[stations.in_london].groupby("crs", sort=False).agg(name=("name", "first"), atcos=("atco", list))
    to_id = lambda crs: "910G" + london.atcos[crs][0][4:]
    if args.hubs:
        pairs, cache, out = [(a, b) for a in HUBS for b in HUBS if a != b], HUB_CACHE, Path("data/tfl_hub_transfers.csv")
    else:
        if args.dest not in london.index:
            ap.error(f"{args.dest} is outside Greater London: no TfL final leg")
        pairs = [(a, args.dest) for a in london.index if a not in DESTINATIONS[args.dest]]
        cache = CACHE if args.dest == "LST" else CACHE.with_name(f"{CACHE.name}_{args.dest}")
        out = Path(f"data/tfl_transfers_{args.dest}.csv")
    cache.mkdir(parents=True, exist_ok=True)

    rows, missing = [], []
    for a, b in pairs:
        name, atcos = london.loc[a]
        journey = cached_journey(cache / (f"{a}_{b}.json" if args.hubs else f"{a}.json"), name, atcos, to_id(b))
        key = {"from_crs": a, "to_crs": b} if args.hubs else {"crs": a, "name": name}
        if journey is None and args.hubs and {a, b} in SAME_STATION:
            rows.append({**key, "tfl_min": 0, "modes": "walking", "summary": "walk (same station)"})
        elif journey is None:
            missing.append(f"{a}->{b}" if args.hubs else a)
        else:
            rows.append({
                **key,
                "tfl_min": int(journey["duration"]),
                "modes": "+".join(leg["mode"]["id"] for leg in journey["legs"]),
                "summary": summarise(journey),
            })

    pd.DataFrame(rows).to_csv(out, index=False)
    print(len(rows), "rows ->", out, "; no journey:", missing)


if __name__ == "__main__":
    main()
