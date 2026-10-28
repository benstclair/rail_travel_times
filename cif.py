import io
import zipfile
from datetime import timedelta
from pathlib import Path

import pandas as pd

DEFAULT_MIN_CHANGE = 5
PASSENGER_STATUS = "P1"
NON_PASSENGER_CATEGORY_PREFIXES = "EBSDZJP"
STP_RANK = {"P": 1, "N": 2, "O": 3, "C": 4}
DATE_IND_SHIFT = {"S": 0, "N": 1, "P": -1}
CONNECTION_COLUMNS = ["trip_id", "seq", "from_tiploc", "to_tiploc", "dep", "arr", "uid", "toc", "headcode", "board_ok", "alight_ok"]
ASSOCIATION_COLUMNS = ["trip_a", "trip_b", "tiploc", "category"]

_flag_cache = {}


def _lines(path, suffix):
    path = Path(path)
    if path.is_dir():
        file = next(p for p in sorted(path.iterdir()) if p.name.lower().removesuffix(".txt").endswith(suffix))
        with open(file, encoding="latin-1") as f:
            yield from (line.rstrip("\r\n") for line in f)
    else:
        with zipfile.ZipFile(path) as zf:
            name = next(n for n in zf.namelist() if n.lower().removesuffix(".txt").endswith(suffix))
            with io.TextIOWrapper(zf.open(name), encoding="latin-1") as f:
                yield from (line.rstrip("\r\n") for line in f)


def _yymmdd(d):
    return d.year % 100 * 10000 + d.month * 100 + d.day


def _minutes(text):
    return None if text == "0000" or not text.isdigit() else int(text[:2]) * 60 + int(text[2:4])


def _flags(activity):
    flags = _flag_cache.get(activity, False)
    if flags is False:
        codes = {activity[i:i + 2].strip() for i in range(0, 12, 2)} - {"", "TB", "TF"}
        board, alight = bool(codes & {"T", "U", "R"}), bool(codes & {"T", "D", "R"})
        flags = (board, alight) if board or alight else (True, True) if not codes else None
        _flag_cache[activity] = flags
    return flags


def _unwrap_midnight(times):
    out, last, offset = [], 0, 0
    for t in times:
        t += offset
        if t < last:
            if last - t > 720:
                offset += 1440
                t += 1440
            else:
                t = last
        out.append(t)
        last = t
    return out


def _applies(start, end, days, ymd, weekday):
    return start <= ymd <= end and days[weekday] == "1"


def load_timetable(zip_path, service_date):
    run_dates = {-1440: service_date - timedelta(days=1), 0: service_date}
    keys = {shift: (_yymmdd(d), d.weekday()) for shift, d in run_dates.items()}
    candidates = {}
    association_records = []
    current = None

    def store(uid, shifts, rank, value):
        for shift in shifts:
            old = candidates.get((uid, shift))
            if old is None or rank >= old[0]:
                candidates[(uid, shift)] = (rank, value)

    def flush():
        if current is not None:
            uid, shifts, rank, value = current
            store(uid, shifts, rank, value)

    for line in _lines(zip_path, "mca"):
        rec = line[:2]
        if rec == "BS":
            flush()
            current = None
            line = line.ljust(80)
            if line[2] == "D":
                continue
            start, end, days = int(line[9:15]), int(line[15:21]), line[21:28]
            shifts = [s for s, (ymd, wd) in keys.items() if _applies(start, end, days, ymd, wd)]
            if not shifts:
                continue
            uid, stp, status, category = line[3:9], line[79], line[29], line[30:32]
            rank = STP_RANK[stp]
            if stp == "C" or status not in PASSENGER_STATUS or category[0] in NON_PASSENGER_CATEGORY_PREFIXES:
                store(uid, shifts, rank, None)
            else:
                current = (uid, shifts, rank, [stp, "", line[32:36], []])
        elif rec == "AA":
            association_records.append(line.ljust(80))
        elif current is None:
            continue
        elif rec == "BX":
            current[3][1] = line[11:13]
        elif rec in ("LO", "LI", "LT"):
            stops = current[3][3]
            if rec == "LO":
                dep, flags = _minutes(line[15:19]), _flags(line[29:41].ljust(12))
                if dep is not None and flags:
                    stops.append((line[2:9].rstrip(), dep, dep, flags[0], False))
            elif rec == "LI":
                arr, dep = _minutes(line[25:29]), _minutes(line[29:33])
                flags = _flags(line[42:54].ljust(12))
                if (arr is not None or dep is not None) and flags:
                    stops.append((line[2:9].rstrip(), dep if arr is None else arr, arr if dep is None else dep, *flags))
            else:
                arr, flags = _minutes(line[15:19]), _flags(line[25:37].ljust(12))
                if arr is not None and flags:
                    stops.append((line[2:9].rstrip(), arr, arr, False, flags[1]))
    flush()

    columns = {name: [] for name in CONNECTION_COLUMNS}
    trip_ids = {}
    for (uid, shift), (_, value) in candidates.items():
        if value is None:
            continue
        stp, toc, headcode, stops = value
        run_date = run_dates[shift]
        trip_id = f"{uid}_{stp}_{run_date:%Y%m%d}"
        times = _unwrap_midnight([t for stop in stops for t in (stop[1], stop[2])])
        for i in range(len(stops) - 1):
            dep, arr = times[2 * i + 1] + shift, times[2 * i + 2] + shift
            if shift and arr <= 0:
                continue
            for name, v in zip(CONNECTION_COLUMNS, (trip_id, i, stops[i][0], stops[i + 1][0], dep, arr, uid, toc, headcode, stops[i][3], stops[i + 1][4])):
                columns[name].append(v)
            trip_ids[(uid, run_date)] = trip_id
    connections = pd.DataFrame(columns).astype({"seq": "int32", "dep": "int32", "arr": "int32"})
    connections = connections.sort_values("dep", kind="stable", ignore_index=True)
    return connections, _associations(association_records, trip_ids, service_date)


def _associations(records, trip_ids, service_date):
    winners = {}
    for line in records:
        if line[2] == "D":
            continue
        base, assoc, start, end, days = line[3:9], line[9:15], int(line[15:21]), int(line[21:27]), line[27:34]
        category, shift = line[34:36], DATE_IND_SHIFT.get(line[36], 0)
        location, stp = line[37:44].strip(), line[79]
        if stp != "C" and (category not in ("JJ", "VV") or line[47] != "P"):
            continue
        for offset in range(-2, 2):
            base_date = service_date + timedelta(days=offset)
            assoc_date = base_date + timedelta(days=shift)
            if (base, base_date) not in trip_ids or (assoc, assoc_date) not in trip_ids:
                continue
            if not _applies(start, end, days, _yymmdd(base_date), base_date.weekday()):
                continue
            key = (base, assoc, base_date, location, line[44], line[45])
            rank = STP_RANK[stp]
            if key not in winners or rank >= winners[key][0]:
                winners[key] = (rank, trip_ids[(base, base_date)], trip_ids[(assoc, assoc_date)], category)
    # trip_a -> trip_b is the direction a seated passenger moves: assoc joins base (JJ), base divides into assoc (VV)
    rows = {(b, a, key[3], category) if category == "JJ" else (a, b, key[3], category)
            for key, (rank, a, b, category) in winners.items() if rank != STP_RANK["C"]}
    return pd.DataFrame(sorted(rows), columns=ASSOCIATION_COLUMNS)


def load_stations_meta(zip_path):
    rows = []
    for line in _lines(zip_path, "msn"):
        if line[:1] != "A" or "FILE-SPEC" in line[:45]:
            continue
        line = line.ljust(66)
        tiploc = line[36:43].strip()
        if tiploc:
            rows.append((tiploc, line[49:52].strip(), line[5:35].strip(), int(line[63:65].strip() or DEFAULT_MIN_CHANGE)))
    meta = pd.DataFrame(rows, columns=["tiploc", "crs", "name", "min_change_min"])
    return meta.drop_duplicates("tiploc", ignore_index=True)
