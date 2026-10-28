import datetime
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cif

FIXTURE = Path(__file__).parent / "fixtures" / "sample"
SERVICE_DATE = datetime.date(2025, 6, 11)


@pytest.fixture(scope="module")
def timetable():
    return cif.load_timetable(FIXTURE, SERVICE_DATE)


def hops(connections, uid, run_date="20250611"):
    rows = connections[connections.trip_id.str.endswith(run_date) & (connections.uid == uid)].sort_values("seq")
    return list(zip(rows.from_tiploc, rows.to_tiploc, rows.dep, rows.arr))


def test_fixture_line_lengths():
    assert all(len(line) == 80 for line in (FIXTURE / "sample.MCA").read_text().splitlines())
    assert all(len(line) == 82 for line in (FIXTURE / "sample.MSN").read_text().splitlines() if line.startswith("A ") and "FILE-SPEC" not in line)


def test_columns_and_sorted(timetable):
    connections, associations = timetable
    assert list(connections.columns[:9]) == ["trip_id", "seq", "from_tiploc", "to_tiploc", "dep", "arr", "uid", "toc", "headcode"]
    assert list(associations.columns) == ["trip_a", "trip_b", "tiploc", "category"]
    assert connections.dep.is_monotonic_increasing


def test_overlay_replaces_permanent_and_pass_is_skipped(timetable):
    connections, _ = timetable
    assert hops(connections, "A00001") == [("COLCHST", "CHLMSFD", 440, 470), ("CHLMSFD", "SHENFLD", 471, 505), ("SHENFLD", "LIVST", 506, 523)]
    assert set(connections[connections.uid == "A00001"].trip_id) == {"A00001_O_20250611"}
    assert not (connections.from_tiploc == "INGTSTN").any() and not (connections.to_tiploc == "INGTSTN").any()
    assert set(connections[connections.uid == "A00001"].toc) == {"GE"}
    assert set(connections[connections.uid == "A00001"].headcode) == {"1A01"}


def test_cancellation_and_other_permanent(timetable):
    connections, _ = timetable
    assert "B00002" not in set(connections.uid)
    assert hops(connections, "B00003") == [("CAMBDGE", "LIVST", 570, 630)]


def test_new_stp(timetable):
    connections, _ = timetable
    assert set(connections[connections.uid == "N00015"].trip_id) == {"N00015_N_20250611"}


def test_wrong_day_of_week_excluded(timetable):
    assert "D00004" not in set(timetable[0].uid)


def test_non_passenger_excluded(timetable):
    uids = set(timetable[0].uid)
    assert "G00007" not in uids
    assert "E00016" not in uids
    assert "H00008" not in uids


def test_pickup_setdown_flags(timetable):
    rows = timetable[0][timetable[0].uid == "F00005"].sort_values("seq")
    assert list(rows.board_ok) == [True, False, True]
    assert list(rows.alight_ok) == [True, False, True]


def test_over_midnight_previous_day(timetable):
    connections, _ = timetable
    assert hops(connections, "S00009", "20250610") == [("IPSWICH", "CHLMSFD", -18, 20), ("CHLMSFD", "LIVST", 22, 60)]
    assert hops(connections, "S00009") == [("NRCH", "IPSWICH", 1380, 1420), ("IPSWICH", "CHLMSFD", 1422, 1460), ("CHLMSFD", "LIVST", 1462, 1500)]
    assert not connections.trip_id.str.endswith("20250610")[connections.uid != "S00009"].any()


def test_associations(timetable):
    _, associations = timetable
    assert associations.to_dict("records") == [{"trip_a": "L00011_P_20250611", "trip_b": "K00010_P_20250611", "tiploc": "CHLMSFD", "category": "JJ"}]


def test_association_dropped_when_trip_missing(timetable):
    assert not timetable[1].trip_b.str.startswith("Z99999").any()


def test_other_date_changes_validity():
    connections, _ = cif.load_timetable(FIXTURE, datetime.date(2025, 6, 10))
    assert "A00001_P_20250610" in set(connections.trip_id)
    assert "B00002_P_20250610" in set(connections.trip_id)
    assert "N00015" not in set(connections.uid)
    assert "H00008_P_20250610" in set(connections.trip_id)


def test_weekend_runs_weekend_only_schedule():
    connections, _ = cif.load_timetable(FIXTURE, datetime.date(2025, 6, 14))
    assert set(connections.uid) == {"D00004", "S00009"}
    assert set(connections[connections.uid == "S00009"].trip_id) == {"S00009_P_20250613"}


def test_zip_matches_directory(tmp_path, timetable):
    archive = shutil.make_archive(str(tmp_path / "ttis"), "zip", FIXTURE)
    connections, associations = cif.load_timetable(archive, SERVICE_DATE)
    assert connections.equals(timetable[0]) and associations.equals(timetable[1])
    assert len(cif.load_stations_meta(archive)) == 8


def test_stations_meta():
    meta = cif.load_stations_meta(FIXTURE)
    assert list(meta.columns) == ["tiploc", "crs", "name", "min_change_min"]
    row = meta.set_index("tiploc").loc["LIVST"]
    assert (row.crs, row["name"], row.min_change_min) == ("LST", "LONDON LIVERPOOL STREET", 15)
    assert meta.tiploc.is_unique and meta.min_change_min.dtype.kind == "i"
