import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from router import NO_HUBS, compute_journeys

STATIONS = pd.DataFrame(
    [(f'{c}X', c, f'{c} name', 51.0 + i / 10, -1.0 + i / 10) for i, c in enumerate(['AAA', 'BBB', 'CCC', 'KGX'])]
    + [('BBY', 'BBB', 'BBB name', 0.0, 0.0), ('LSTX', 'LST', 'Liverpool Street', 51.5, -0.08),
       ('LSTE', 'LST', 'Liverpool Street', 51.5, -0.08)],
    columns=['tiploc', 'crs', 'name', 'lat', 'lon'])
NO_ASSOC = pd.DataFrame(columns=['trip_a', 'trip_b', 'tiploc', 'category'])
NO_TFL = pd.DataFrame(columns=['crs', 'name', 'tfl_min', 'modes', 'summary'])
KGX_TFL = pd.DataFrame([('KGX', 'Kings Cross', 14, 'tube', 'Northern line to Moorgate, walk')], columns=NO_TFL.columns)


def train(trip, stops):
    rows = [(trip, k, f, t, d, a, trip, 'LE', trip) for k, ((f, _, d), (t, a, _)) in enumerate(zip(stops, stops[1:]))]
    return pd.DataFrame(rows, columns=['trip_id', 'seq', 'from_tiploc', 'to_tiploc', 'dep', 'arr', 'uid', 'toc', 'headcode'])


def run(trains, assoc=NO_ASSOC, tfl=NO_TFL, min_change=None, window=(480, 540), **kwargs):
    connections = pd.concat(trains, ignore_index=True).sort_values('dep', ignore_index=True)
    return compute_journeys(connections, assoc, STATIONS, min_change or {}, tfl, window, **kwargs).set_index('crs')


def test_direct():
    res = run([train('t1', [('AAAX', None, 450), ('LSTX', 500, None)])])
    row = res.loc['AAA']
    assert (row.depart, row.arrive, row.duration_min, row.n_changes, row.tfl_min) == ('07:30', '08:20', 50, 0, 0)
    assert row.arrival_station == 'Liverpool Street' and row.changes_at == ''
    assert row.legs == 'AAA 07:30 -> LST 08:20 [LE t1]'
    assert 'LST' not in res.index


@pytest.mark.parametrize('second_dep, ok', [(475, True), (474, False)])
def test_change_mct(second_dep, ok):
    res = run([train('t1', [('AAAX', None, 440), ('BBBX', 470, None)]),
               train('t2', [('BBBX', None, second_dep), ('LSTX', 500, None)])], min_change={'BBB': 5})
    assert ('AAA' in res.index) == ok
    assert 'BBB' in res.index
    if ok:
        assert res.loc['AAA'].n_changes == 1 and res.loc['AAA'].changes_at == 'BBB name'
        assert res.loc['AAA'].legs == 'AAA 07:20 -> BBB 07:50 [LE t1] | BBB 07:55 -> LST 08:20 [LE t2]'


def test_stay_on_train_no_change_time():
    res = run([train('t1', [('AAAX', None, 440), ('BBBX', 470, 471), ('LSTX', 500, None)])], min_change={'BBB': 30})
    assert res.loc['AAA'].n_changes == 0 and res.loc['AAA'].duration_min == 60


def test_association_is_stay_seated():
    trains = [train('t1', [('AAAX', None, 440), ('BBBX', 470, None)]), train('t2', [('BBBX', None, 471), ('LSTX', 500, None)])]
    assoc = pd.DataFrame([('t1', 't2', 'BBBX', 'JJ')], columns=NO_ASSOC.columns)
    assert 'AAA' not in run(trains).index
    row = run(trains, assoc).loc['AAA']
    assert row.n_changes == 0 and row.changes_at == '' and row.duration_min == 60
    assert row.legs.count('|') == 1


def test_assoc_not_usable_if_second_leaves_before_arrival():
    trains = [train('t1', [('AAAX', None, 440), ('BBBX', 472, None)]), train('t2', [('BBBX', None, 471), ('LSTX', 500, None)])]
    assoc = pd.DataFrame([('t1', 't2', 'BBBX', 'JJ')], columns=NO_ASSOC.columns)
    assert 'AAA' not in run(trains, assoc).index


@pytest.mark.parametrize('direct_arr, expected_tfl', [(500, 19), (490, 0)])
def test_tfl_vs_direct(direct_arr, expected_tfl):
    res = run([train('t1', [('AAAX', None, 450), ('KGXX', 480, None)]),
               train('t2', [('AAAX', None, 450), ('LSTX', direct_arr, None)])], tfl=KGX_TFL)
    row = res.loc['AAA']
    assert row.tfl_min == expected_tfl
    if expected_tfl:
        assert row.duration_min == 49 and row.arrival_station == 'KGX name' and row.n_changes == 0
        assert row.legs.endswith(' | TfL 19m: Northern line to Moorgate, walk')
    else:
        assert row.duration_min == 40 and row.arrival_station == 'Liverpool Street'


def test_tfl_arrival_must_be_in_window():
    assert 'AAA' not in run([train('t1', [('AAAX', None, 450), ('KGXX', 460, None)])], tfl=KGX_TFL).index
    assert 'AAA' in run([train('t1', [('AAAX', None, 450), ('KGXX', 461, None)])], tfl=KGX_TFL).index
    assert 'AAA' not in run([train('t1', [('AAAX', None, 450), ('KGXX', 522, None)])], tfl=KGX_TFL).index
    res = run([train('t1', [('AAAX', None, 450), ('KGXX', 521, None)])], tfl=KGX_TFL)
    assert res.loc['AAA'].arrive == '09:00'


@pytest.mark.parametrize('arr, ok', [(479, False), (480, True), (540, True), (541, False)])
def test_window_boundaries(arr, ok):
    res = run([train('t1', [('AAAX', None, 400), ('LSTX', arr, None)])])
    assert ('AAA' in res.index) == ok


def test_earlier_arrival_outside_window_does_not_mask_in_window_option():
    res = run([train('t1', [('AAAX', None, 440), ('LSTX', 470, None)]), train('t2', [('AAAX', None, 400), ('LSTX', 485, None)])])
    assert res.loc['AAA'].arrive == '08:05'


def test_fastest_beats_latest_departure():
    res = run([train('slow', [('AAAX', None, 470), ('LSTX', 540, None)]), train('fast', [('AAAX', None, 450), ('LSTX', 485, None)])])
    assert (res.loc['AAA'].duration_min, res.loc['AAA'].depart) == (35, '07:30')


def test_tiebreak_fewer_changes_then_later_departure():
    res = run([train('a', [('AAAX', None, 440), ('BBBX', 460, None)]), train('b', [('BBBX', None, 470), ('LSTX', 500, None)]),
               train('c', [('AAAX', None, 440), ('LSTX', 500, None)])])
    assert res.loc['AAA'].n_changes == 0
    res = run([train('x', [('AAAX', None, 430), ('LSTX', 490, None)]), train('y', [('AAAX', None, 440), ('LSTX', 500, None)])])
    assert res.loc['AAA'].depart == '07:20'


def test_multi_tiploc_station_grouping():
    trains = [train('t1', [('AAAX', None, 440), ('BBBX', 470, None)]), train('t2', [('BBY', None, 476), ('LSTX', 500, None)])]
    res = run(trains)
    assert res.loc['AAA'].n_changes == 1 and res.loc['AAA'].changes_at == 'BBB name'
    assert 'AAA' not in run([trains[0], train('t2', [('BBY', None, 474), ('LSTX', 500, None)])]).index
    assert list(res.index).count('BBB') == 1 and res.loc['BBB'].depart == '07:56'


@pytest.mark.parametrize('second_dep, ok', [(475, True), (474, False)])
def test_change_between_sibling_crs(second_dep, ok):
    trains = [train('t1', [('AAAX', None, 440), ('BBBX', 470, None)]), train('t2', [('CCCX', None, second_dep), ('LSTX', 500, None)])]
    assert 'AAA' not in run(trains, min_change={'BBB': 5}).index
    res = run(trains, min_change={'BBB': 5}, siblings={'BBB': ['CCC'], 'CCC': ['BBB']})
    assert ('AAA' in res.index) == ok
    if ok:
        assert res.loc['AAA'].n_changes == 1 and res.loc['AAA'].changes_at == 'BBB name'


def test_elizabeth_line_tiploc_is_destination():
    assert run([train('t1', [('AAAX', None, 450), ('LSTE', 500, None)])]).loc['AAA'].n_changes == 0


def test_negative_times():
    res = run([train('t1', [('AAAX', None, -10), ('LSTX', 500, None)])])
    assert res.loc['AAA'].depart == '23:50'


def test_performance():
    rng = np.random.default_rng(0)
    n_trips, hops, n_stations = 30000, 10, 2500
    codes = [f'S{i:04d}' for i in range(n_stations)]
    stations = pd.DataFrame({'tiploc': codes + ['LSTX'], 'crs': codes + ['LST'], 'name': codes + ['Liverpool Street'],
                             'lat': 51.0, 'lon': 0.0})
    stops = rng.integers(0, n_stations, (n_trips, hops + 1))
    stops[rng.random(n_trips) < 0.5, -1] = n_stations
    starts = rng.integers(180, 500, n_trips)
    gaps = rng.integers(3, 12, (n_trips, hops))
    arrs = starts[:, None] + gaps.cumsum(axis=1) + np.arange(hops)[None, :]
    deps = np.hstack([starts[:, None], arrs[:, :-1] + 1])
    tip = np.array(codes + ['LSTX'])
    connections = pd.DataFrame({
        'trip_id': np.repeat(np.arange(n_trips), hops), 'seq': np.tile(np.arange(hops), n_trips),
        'from_tiploc': tip[stops[:, :-1]].ravel(), 'to_tiploc': tip[stops[:, 1:]].ravel(),
        'dep': deps.ravel(), 'arr': arrs.ravel(), 'uid': 'u', 'toc': 'LE', 'headcode': 'h'})
    connections = connections[(connections.dep >= 180) & (connections.dep <= 540)].sort_values('dep', ignore_index=True)
    mct = {c: 3 for c in codes[::2]}
    start = time.perf_counter()
    res = compute_journeys(connections, NO_ASSOC, stations, mct, NO_TFL)
    elapsed = time.perf_counter() - start
    print(f'\n{len(connections)} connections -> {len(res)} origins in {elapsed:.1f}s')
    assert len(res) > 0 and elapsed < 120


def brute_force(connections, mct, origin_tiploc):
    by_trip = {t: g.sort_values('seq').to_dict('records') for t, g in connections.groupby('trip_id')}
    best = []

    def ride(trip, k, start, changes, dep0):
        hops = by_trip[trip]
        for j in range(k, len(hops)):
            c = hops[j]
            if c['to_tiploc'] == 'LSTX' and 480 <= c['arr'] <= 540:
                best.append((c['arr'] - dep0, changes, -dep0))
            for t2, h2 in by_trip.items():
                for k2, c2 in enumerate(h2):
                    if t2 != trip and c2['from_tiploc'] == c['to_tiploc'] and c2['dep'] >= c['arr'] + mct.get(c['to_tiploc'][:3], 5) and changes < 4:
                        ride(t2, k2, None, changes + 1, dep0)

    for t, hops in by_trip.items():
        for k, c in enumerate(hops):
            if c['from_tiploc'] == origin_tiploc:
                ride(t, k, None, 0, c['dep'])
    return min(best) if best else None


def test_matches_brute_force_on_random_small_networks():
    rng = np.random.default_rng(1)
    names = ['AAA', 'BBB', 'CCC', 'DDD', 'EEE']
    stations = pd.DataFrame({'tiploc': [f'{n}X' for n in names] + ['LSTX'], 'crs': names + ['LST'],
                             'name': names + ['Liverpool Street'], 'lat': 0.0, 'lon': 0.0})
    tips = [f'{n}X' for n in names] + ['LSTX']
    mct = {'BBB': 3, 'CCC': 7, 'DDD': 0}
    for _ in range(60):
        trains = []
        for t in range(10):
            stops = list(rng.choice(tips[:-1], rng.integers(2, 4), replace=False))
            if rng.random() < 0.5:
                stops.append('LSTX')
            time_ = int(rng.integers(430, 520))
            seq = [(stops[0], None, time_)]
            for s_ in stops[1:]:
                time_ += int(rng.integers(3, 20))
                seq.append((s_, time_, time_ + int(rng.integers(0, 3))))
                time_ = seq[-1][2]
            trains.append(train(f't{t}', [(s_, a, d) for s_, a, d in seq]))
        connections = pd.concat(trains, ignore_index=True).sort_values('dep', ignore_index=True)
        res = compute_journeys(connections, NO_ASSOC, stations, mct, NO_TFL)
        for name in names:
            expected = brute_force(connections, mct, f'{name}X')
            if expected is None:
                assert name not in set(res.crs)
            else:
                row = res[res.crs == name].iloc[0]
                assert (row.duration_min, row.n_changes) == expected[:2]


def test_tfl_only_at_london_tiploc():
    stations = pd.concat([STATIONS.assign(in_london=STATIONS.crs.isin(['KGX', 'LST'])),
                          pd.DataFrame([('KGXFAR', 'KGX', 'Far away', 54.0, -2.0, False)], columns=[*STATIONS.columns, 'in_london'])])
    connections = train('t1', [('AAAX', None, 450), ('KGXFAR', 480, None)])
    res = compute_journeys(connections, NO_ASSOC, stations, {}, KGX_TFL).set_index('crs')
    assert 'AAA' not in res.index


def test_board_alight_restrictions():
    t1 = train('t1', [('AAAX', None, 440), ('BBBX', 470, 471), ('LSTX', 500, None)])
    res = run([t1.assign(board_ok=[True, False], alight_ok=True)])
    assert 'AAA' in res.index and 'BBB' not in res.index
    t2 = train('t2', [('CCCX', None, 440), ('BBBX', 460, None)]).assign(board_ok=True, alight_ok=False)
    t3 = train('t3', [('BBBX', None, 470), ('LSTX', 500, None)]).assign(board_ok=True, alight_ok=True)
    assert 'CCC' not in run([t2, t3]).index


def test_elizabeth_line_lsx_is_destination():
    stations = pd.concat([STATIONS, pd.DataFrame([('LIVSTLL', 'LSX', 'Liverpool Street', 51.5, -0.08)], columns=STATIONS.columns)])
    connections = train('t1', [('AAAX', None, 450), ('LIVSTLL', 500, None)])
    res = compute_journeys(connections, NO_ASSOC, stations, {}, NO_TFL).set_index('crs')
    assert res.loc['AAA'].duration_min == 50 and 'LSX' not in res.index


def test_other_destination():
    trains = [train('t1', [('AAAX', None, 450), ('CCCX', 500, None)]), train('t2', [('BBBX', None, 450), ('LSTX', 500, None)])]
    res = run(trains, dest_crs={'CCC'})
    assert list(res.index) == ['AAA'] and res.loc['AAA'].arrival_station == 'CCC name'


@pytest.mark.parametrize('second_dep, ok', [(500, True), (499, False)])
def test_hub_transfer(second_dep, ok):
    hubs = pd.DataFrame([('KGX', 'CCC', 20, 'walking', 'walk')], columns=NO_HUBS.columns)
    trains = [train('t1', [('AAAX', None, 440), ('KGXX', 470, None)]), train('t2', [('CCCX', None, second_dep), ('LSTX', 530, None)])]
    res = run(trains, hubs=hubs, min_change={'KGX': 30})
    assert ('AAA' in res.index) == ok
    if ok:
        row = res.loc['AAA']
        assert row.n_changes == 1 and row.changes_at == 'KGX name -> CCC name (TfL 30m)'
        assert row.legs == f'AAA 07:20 -> KGX 07:50 [LE t1] | TfL 30m: walk | CCC 08:{second_dep - 480:02d} -> LST 08:50 [LE t2]'
    no_london = STATIONS.assign(in_london=STATIONS.crs == 'LST')
    connections = pd.concat(trains, ignore_index=True).sort_values('dep', ignore_index=True)
    assert 'AAA' not in set(compute_journeys(connections, NO_ASSOC, no_london, {}, NO_TFL, hubs=hubs).crs)


@pytest.mark.parametrize('arrival, ok', [(330, True), (329, False)])
def test_no_hub_hop_before_tfl_first_departure(arrival, ok):
    hubs = pd.DataFrame([('KGX', 'CCC', 20, 'walking', 'walk')], columns=NO_HUBS.columns)
    trains = [train('t1', [('AAAX', None, 300), ('KGXX', arrival, None)]), train('t2', [('CCCX', None, 400), ('LSTX', 500, None)])]
    assert ('AAA' in run(trains, hubs=hubs).index) == ok
    # a journey starting with a hop: KGX -> CCC (30 min) to board at CCC 05:59 / 06:00
    start = run([train('t1', [('CCCX', None, arrival + 30), ('LSTX', 500, None)])], hubs=hubs)
    assert ('KGX' in start.index) == ok


def test_tfl_only_journey_from_london_origin():
    res = run([train('t1', [('KGXX', None, 470), ('LSTX', 520, None)])], tfl=KGX_TFL)
    row = res.loc['KGX']  # TfL 14 + 5 = 19 beats the 50-min train
    assert (row.duration_min, row.depart, row.arrive, row.n_changes, row.tfl_min) == (19, '08:41', '09:00', 0, 19)
    assert row.legs == 'TfL 19m: Northern line to Moorgate, walk' and row['name'] == 'KGX name'
    assert run([train('t1', [('KGXX', None, 500), ('LSTX', 515, None)])], tfl=KGX_TFL).loc['KGX'].tfl_min == 0


def test_journey_can_start_with_hub_hop():
    hubs = pd.DataFrame([('KGX', 'CCC', 20, 'walking', 'walk')], columns=NO_HUBS.columns)
    res = run([train('t1', [('CCCX', None, 470), ('LSTX', 500, None)]), train('t2', [('KGXX', None, 430), ('LSTX', 510, None)])], hubs=hubs)
    row = res.loc['KGX']  # 30-min hop + 30-min train beats the 80-min train
    assert (row.duration_min, row.depart, row.n_changes, row['name']) == (60, '07:20', 0, 'KGX name')
    assert row.legs == 'TfL 30m: walk | CCC 07:50 -> LST 08:20 [LE t1]'


@pytest.mark.parametrize('own_dep, walk_wins', [(None, True), (430, True), (466, False)])
def test_journey_can_start_from_sibling_crs(own_dep, walk_wins):
    trains = [train('t1', [('CCCX', None, 470), ('LSTX', 500, None)])]
    if own_dep:
        trains.append(train('t2', [('BBBX', None, own_dep), ('LSTX', 500, None)]))
    res = run(trains, min_change={'BBB': 5, 'CCC': 5}, siblings={'BBB': ['CCC'], 'CCC': ['BBB']})
    row = res.loc['BBB']
    if walk_wins:  # 5-min change time + 30-min train from the sibling code; not counted as a change
        assert (row.duration_min, row.depart, row.n_changes, row['name']) == (35, '07:45', 0, 'BBB name')
        assert row.legs == 'walk CCC (5m) | CCC 07:50 -> LST 08:20 [LE t1]'
    else:
        assert row.duration_min == 34 and row.legs == 'BBB 07:46 -> LST 08:20 [LE t2]'
