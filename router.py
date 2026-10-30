import argparse
import datetime
from bisect import bisect_right
from pathlib import Path

import pandas as pd

STAY, END, XFER, ASSOC, HUB = 0, 1, 2, 3, 4
DEFAULT_MCT = 5
# TfL durations start at the first tube departure: add platform walks and the wait
HUB_ALLOWANCE = 10  # mid-journey: off the train and onto the next one
FINAL_ALLOWANCE = 5  # final leg: off the train only
TFL_FIRST_DEP = 330  # 05:30: no TfL hop starts earlier (durations are fixed; most tube lines aren't running yet)
DESTINATIONS = {'LST': {'LST', 'LSX'},  # main line + Elizabeth line low-level
                'DMK': {'DMK'}, 'OXF': {'OXF'}, 'EXD': {'EXD'}}
# London terminals plus same-place split codes (Elizabeth line / low-level), then interchanges chosen by measured
# journey-time gain on the 2 Feb 2027 timetable; linked pairwise by TfL
HUBS = ['PAD', 'PDX', 'MYB', 'EUS', 'STP', 'SPL', 'KGX', 'LST', 'LSX', 'FST', 'CST', 'MOG', 'CTK', 'BFR', 'CHX',
        'WAT', 'WAE', 'LBG', 'VIC', 'ZFD', 'FDX',
        'WEH', 'WIM', 'ZWL', 'TOM', 'BDS', 'CLJ', 'HRW', 'EPH', 'HHY', 'WHP',
        'TCR', 'CWX']  # Elizabeth line interchanges: TfL legs exclude the Elizabeth line, so join its timetabled trains here
NO_HUBS = pd.DataFrame(columns=['from_crs', 'to_crs', 'tfl_min', 'modes', 'summary'])
COLUMNS = ['crs', 'name', 'lat', 'lon', 'depart', 'arrive', 'duration_min', 'n_changes',
           'changes_at', 'arrival_station', 'tfl_min', 'legs']


def fmt_time(minutes):
    return f'{minutes % 1440 // 60:02d}:{minutes % 1440 % 60:02d}'


def compute_journeys(connections, associations, stations, min_change, tfl, window=(480, 540),
                     dest_crs=DESTINATIONS['LST'], hubs=NO_HUBS, siblings=None):
    # siblings: CRS -> other CRS codes at the same station (one MSN station split across NaPTAN CRS codes)
    siblings = siblings or {}
    lo, hi = window
    crs_of = dict(zip(stations.tiploc, stations.crs))
    by_tiploc = stations.drop_duplicates('tiploc').set_index('tiploc')
    crs_name = dict(zip(stations.crs[::-1], stations.name[::-1]))  # first TIPLOC's name per CRS
    dest_tiplocs = set(stations.tiploc[stations.crs.isin(dest_crs)])
    # TfL legs keyed by CRS, but only valid at London TIPLOCs (NaPTAN shares NRC between Newbury Racecourse and Newbury Park)
    london_tiplocs = set(stations.tiploc[stations.in_london]) if 'in_london' in stations else set(stations.tiploc)
    tfl_min = dict(zip(tfl.crs, tfl.tfl_min.astype(int) + FINAL_ALLOWANCE)) if len(tfl) else {}
    tfl_summary = dict(zip(tfl.crs, tfl.summary)) if len(tfl) else {}
    hub_out = {}
    for a, b, t in zip(hubs.from_crs, hubs.to_crs, hubs.tfl_min.astype(int)):
        hub_out.setdefault(a, []).append((b, t + HUB_ALLOWANCE))
    hub_summary = dict(zip(zip(hubs.from_crs, hubs.to_crs), hubs.summary))

    cons = connections[connections.arr <= hi].sort_values(['dep', 'seq'], ascending=False)
    trips, dep, arr = cons.trip_id.tolist(), cons.dep.tolist(), cons.arr.tolist()
    from_tip, to_tip = cons.from_tiploc.tolist(), cons.to_tiploc.tolist()
    from_crs = [crs_of.get(t, t) for t in from_tip]
    to_crs = [crs_of.get(t, t) for t in to_tip]
    toc, headcode = cons.toc.tolist(), cons.headcode.tolist()
    board_ok = cons.board_ok.tolist() if 'board_ok' in cons else [True] * len(cons)
    alight_ok = cons.alight_ok.tolist() if 'alight_ok' in cons else [True] * len(cons)

    assoc_next = {}
    for a, b, tip in zip(associations.trip_a, associations.trip_b, associations.tiploc):
        assoc_next.setdefault((a, tip), []).append(b)
    assoc_from_trips = {k[0] for k in assoc_next}
    assoc_boardings = {(b, k[1]) for k, bs in assoc_next.items() for b in bs}
    assoc_to_trips = {k[0] for k in assoc_boardings}
    boarding_node = {}

    trip_node = {}
    frontier = {}
    for i, trip in enumerate(trips):
        d, a, v, vc = dep[i], arr[i], to_tip[i], to_crs[i]
        best = trip_node.get(trip)
        if best:
            best_key, kind, ref = best[:2], STAY, best
        else:
            best_key, kind, ref = (hi + 1, 0), None, None
        if alight_ok[i] and v in dest_tiplocs:
            if lo <= a <= hi and (a, 0) < best_key:
                best_key, kind, ref = (a, 0), END, 0
        elif alight_ok[i]:
            in_london = v in london_tiplocs
            t = tfl_min.get(vc) if in_london else None
            if t is not None and lo <= a + t <= hi and (a + t, 0) < best_key:
                best_key, kind, ref = (a + t, 0), END, t
            # same-station change (MCT), then TfL hops to other London hubs (TfL time only); either counts as one change
            ready = a + min_change.get(vc, DEFAULT_MCT)
            hops = [(c, ready, None) for c in (vc, *siblings.get(vc, ()))]
            if in_london and a >= TFL_FIRST_DEP:
                hops += [(b, a + hop_min, hop_min) for b, hop_min in hub_out.get(vc, ())]
            for next_crs, ready, hop_min in hops:
                fr = frontier.get(next_crs)
                if fr:
                    k = bisect_right(fr[0], -ready) - 1
                    if k >= 0:
                        node = fr[1][k]
                        if (node[0], node[1] + 1) < best_key:
                            best_key = (node[0], node[1] + 1)
                            kind, ref = (XFER, node) if hop_min is None else (HUB, (node, next_crs, hop_min))
        if trip in assoc_from_trips:
            for other in assoc_next.get((trip, v), ()):
                rec = boarding_node.get((other, v))
                if rec and rec[0] >= a and rec[1][:2] < best_key:
                    best_key, kind, ref = rec[1][:2], ASSOC, rec[1]
        if kind is None:
            continue
        node = (best_key[0], best_key[1], i, kind, ref)
        trip_node[trip] = node
        if trip in assoc_to_trips and (trip, from_tip[i]) in assoc_boardings:
            boarding_node[(trip, from_tip[i])] = (d, node)
        uc = from_crs[i]
        if uc in dest_crs or not board_ok[i]:
            continue
        fr = frontier.setdefault(uc, ([], []))
        if fr[1]:
            last = fr[1][-1]
            if best_key >= last[:2]:
                continue
            if fr[0][-1] == -d:
                fr[0].pop()
                fr[1].pop()
        fr[0].append(-d)
        fr[1].append(node)

    station_name = lambda t: by_tiploc['name'][t] if t in by_tiploc.index else t
    london_tiploc, first_tiploc = {}, {}  # name/coords for origins whose journey doesn't start with a train there
    for t, c in zip(stations.tiploc, stations.crs):
        first_tiploc.setdefault(c, t)
        if t in london_tiplocs:
            london_tiploc.setdefault(c, t)

    def best(nodes, hop_min=0):  # hop_min: TfL time before boarding the first train
        return min(((n[0] - dep[n[2]] + hop_min, n[1], hop_min - dep[n[2]]), n) for n in nodes)

    # per origin CRS: (key, node, start) with start None (train from here), (hub, minutes, 'TfL') / (sibling, MCT, 'walk')
    # or 'tfl' (TfL all the way)
    options = {crs: (*best(nodes), None) for crs, (_, nodes) in frontier.items()}

    def offer(a, b, minutes, how, nodes):
        if a not in dest_crs and nodes:
            key, node = best(nodes, minutes)
            if a not in options or key < options[a][0]:
                options[a] = (key, node, (b, minutes, how))

    for a, hops in hub_out.items():
        for b, hop_min in hops:
            offer(a, b, hop_min, 'TfL', [n for n in frontier.get(b, ([], []))[1] if dep[n[2]] - hop_min >= TFL_FIRST_DEP])
    for a, sibs in siblings.items():  # start with a train from the other code of a split station; not a change
        for b in sibs:
            offer(a, b, min_change.get(a, DEFAULT_MCT), 'walk', frontier.get(b, ([], []))[1])
    for a, t in tfl_min.items():
        key = (t, 0, t - hi)  # arrive at the end of the window
        if a not in options or key < options[a][0]:
            options[a] = (key, None, 'tfl')

    rows = []
    for crs, ((duration, _, _), node, start) in options.items():
        legs, changes, tfl_leg = [], [], 0
        if start == 'tfl':
            origin, tfl_leg, arrive = london_tiploc.get(crs), tfl_min[crs], hi
            arrival = origin
        else:
            arrive = node[0]
            first_i = first = node[2]
            while True:
                i, kind, ref = node[2], node[3], node[4]
                if kind == STAY:
                    node = ref
                    continue
                legs.append(f'{from_crs[first]} {fmt_time(dep[first])} -> {to_crs[i]} {fmt_time(arr[i])} [{toc[first]} {headcode[first]}]')
                if kind == END:
                    tfl_leg = ref
                    break
                if kind == XFER:
                    changes.append(station_name(to_tip[i]))
                elif kind == HUB:
                    ref, next_crs, hop_min = ref
                    changes.append(f'{station_name(to_tip[i])} -> {crs_name[next_crs]} (TfL {hop_min}m)')
                    legs.append(f'TfL {hop_min}m: {hub_summary[(to_crs[i], next_crs)]}')
                node = ref
                first = node[2]
            arrival = to_tip[i]
            origin = from_tip[first_i] if not start else first_tiploc.get(crs) if start[2] == 'walk' else london_tiploc.get(crs)
            if start:
                b, minutes, how = start
                legs.insert(0, f'walk {b} ({minutes}m)' if how == 'walk' else f'TfL {minutes}m: {hub_summary[(crs, b)]}')
        if origin not in by_tiploc.index:
            continue
        if tfl_leg:
            legs.append(f'TfL {tfl_leg}m: {tfl_summary[crs_of.get(arrival, crs)]}')
        rows.append({
            'crs': crs,
            'name': by_tiploc['name'][origin],
            'lat': by_tiploc['lat'][origin],
            'lon': by_tiploc['lon'][origin],
            'depart': fmt_time(arrive - duration),
            'arrive': fmt_time(arrive),
            'duration_min': duration,
            'n_changes': len(changes),
            'changes_at': '; '.join(changes),
            'arrival_station': station_name(arrival),
            'tfl_min': tfl_leg,
            'legs': ' | '.join(legs),
        })
    return pd.DataFrame(rows, columns=COLUMNS).sort_values('crs', ignore_index=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--timetable', required=True)
    parser.add_argument('--date', required=True)
    parser.add_argument('--dest', nargs='+', choices=DESTINATIONS, default=list(DESTINATIONS))
    args = parser.parse_args()
    from cif import load_timetable, load_stations_meta

    data_dir = Path(__file__).parent / 'data'
    connections, associations = load_timetable(args.timetable, datetime.date.fromisoformat(args.date))
    connections = connections[(connections.dep >= 180) & (connections.dep <= 540)]
    stations = pd.read_csv(data_dir / 'stations.csv')
    hubs = pd.read_csv(data_dir / 'tfl_hub_transfers.csv')
    meta = load_stations_meta(args.timetable)

    # TIPLOCs NaPTAN doesn't list: map via the MSN's CRS and borrow that station's NaPTAN name/coordinates
    by_crs = stations.groupby('crs').agg(name=('name', 'first'), lat=('lat', 'first'), lon=('lon', 'first'), in_london=('in_london', 'all'))
    extra = meta[~meta.tiploc.isin(stations.tiploc)][['tiploc', 'crs']].merge(by_crs, left_on='crs', right_index=True)
    stations = pd.concat([stations, extra], ignore_index=True)
    # changes follow MSN stations: NaPTAN CRS codes sharing an MSN CRS are siblings and take that station's change time
    msn = stations[['tiploc', 'crs']].merge(meta.rename(columns={'crs': 'msn_crs'}), on='tiploc')
    msn['mct'] = msn.groupby('msn_crs').min_change_min.transform('max')
    min_change = msn.groupby('crs').mct.max().astype(int).to_dict()
    siblings = {c: [o for o in group if o != c]
                for group in msn.groupby('msn_crs').crs.unique() if len(group) > 1 for c in group}
    stops = pd.concat([connections.from_tiploc, connections.to_tiploc])
    unmapped = stops[~stops.isin(stations.tiploc)].value_counts()
    print(f'{len(extra)} TIPLOCs mapped via MSN; {len(unmapped)} stop TIPLOCs unmapped (treated as interchange-only):',
          ', '.join(unmapped.index[:30]))
    for dest in args.dest:
        tfl_path = data_dir / f'tfl_transfers_{dest}.csv'  # only Greater London destinations have one
        tfl = pd.read_csv(tfl_path) if tfl_path.exists() else pd.DataFrame(columns=['crs', 'tfl_min', 'summary'])
        results = compute_journeys(connections, associations, stations, min_change, tfl,
                                   dest_crs=DESTINATIONS[dest], hubs=hubs, siblings=siblings)
        out = data_dir / f'results_{dest}.csv'
        results.to_csv(out, index=False)
        print(f'{dest}: {len(results)} stations written to {out}')


if __name__ == '__main__':
    main()
