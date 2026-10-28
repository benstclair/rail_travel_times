# Rail travel times

Interactive map of the fastest scheduled rail journey from every GB station to a selectable destination, arriving between 08:00 and 09:00 on a chosen weekday.
Station-to-station only (no walk/drive access yet).

## Pipeline

| Step | Script | Output |
|---|---|---|
| 1. Stations (NaPTAN) | `python stations.py` | `data/stations.csv` |
| 2a. TfL final leg to each London destination | `python tfl_transfers.py --dest LST` (and `--dest DMK`) | `data/tfl_transfers_<CRS>.csv` |
| 2b. TfL mid-journey transfers between London hubs | `python tfl_transfers.py --hubs` | `data/tfl_hub_transfers.csv` |
| 3. Timetable parse + routing | `python router.py --timetable <RDG timetable zip or dir> --date YYYY-MM-DD [--dest LST DMK OXF EXD]` | `data/results_<CRS>.csv` |
| 4. Map | `python build_map.py [--results-dir data] [--out map.html]` | `map.html` |

Destinations are defined in `router.DESTINATIONS` (CRS -> set of CRS codes counted as arriving; LST = LST + LSX).
The router loads the timetable once and writes one results file per destination (all four by default).
`build_map.py` includes every destination with a `results_<CRS>.csv` in `--results-dir`.

`cif.py` is the timetable parser used by `router.py`. Raw downloads live in `data/raw/` and are never modified.
Tests: `python -m pytest -q tests/`.

Status: all steps have run on real data. Step 3 ran on the RDG "Timetable - Full Refresh - Weekly" zip from the Rail
Data Marketplace (set RJTTF995, generated 20 Oct 2026, schedules 17 May 2026 - 15 May 2027) for Tue 2 Feb 2027 (D2);
`map.html` is rebuilt from those results. The planner spot-check is not yet done (see Open).
34 stop TIPLOCs are unmapped on that date: Tyne & Wear Metro, Sheffield tram and District line stops, none National Rail.
The feed names its files `RJTTF<seq><TYPE>.txt` (e.g. `RJTTF995MCA.txt`); `cif.py` matches the type with or without `.txt`.

## Updating the timetable

1. Download "Timetable - Full Refresh - Weekly" (RDG, Rail Data Marketplace) -- not "Changes Only".
   Save it unchanged under `data/raw/timetable/`; replace or keep the old zip (the router takes the path).
   There is no download script; the router reads the zip (or an extracted folder).
2. Service date: Tue 2 Feb 2027 (D2) while the file covers it. If moving the date, pick a Tue inside the
   file's validity, away from Christmas/bank holidays, and compare a few candidate Tuesdays first
   (engineering works remove stations on some dates, e.g. Chepstow/Lydney on 10 Nov 2026).
3. `python router.py --timetable data/raw/timetable/<file>.zip --date YYYY-MM-DD`. Check the printed line:
   stop TIPLOCs still unmapped (these can be changed at but never appear as origins) should be non-National Rail
   only (Metro, tram, Underground); if a real station appears, add a mapping.
4. Sanity-check `data/results_<CRS>.csv`: station counts per destination close to the previous run,
   no ECS/freight headcodes (only 1/2/9) in `legs`, a few known journeys unchanged or explainable.
5. `python build_map.py` -> `map.html`.

Steps 1-2 of the pipeline don't need re-running. If they are: `tfl_transfers.py` queries a fixed date
(Tue 10 Nov 2026) and TfL only plans future dates, so update `PARAMS`/`DEADLINE` first. Cached responses are reused
(`data/raw/tfl_transfer_modes/` for LST, `data/raw/tfl_transfer_modes_<CRS>/` for other destinations,
`data/raw/tfl_hub_transfers/` for hub pairs); delete a folder to force fresh queries.
The TfL times were queried for Tue 10 Nov 2026 while the rail results are for Tue 2 Feb 2027. TfL can't plan that far
ahead (2 Feb 2027 returns HTTP 500 when queried on 26 Oct 2026; 1 Dec 2026 works), so the dates can only be aligned
closer to the service date. The arrive-by-09:00 filter compares time of day only, so cached responses stay valid.

## Outputs

`data/stations.csv`: `tiploc, crs, name, lat, lon, atco, in_london` -- one row per TIPLOC (several TIPLOCs can share a CRS).
`in_london` = NPTG locality in Greater London (admin area 082).

`data/tfl_transfers_<CRS>.csv` (LST, DMK): `crs, name, tfl_min, modes, summary` -- final leg from each Greater London
station to that destination, from a TfL query arriving by 09:00 on Tue 10 Nov 2026, restricted to tube, DLR,
Overground, Elizabeth line and walking. `tfl_min` is TfL's raw duration (the router adds the allowance).
Oxford and Exeter are outside Greater London and have no TfL final leg.

`data/tfl_hub_transfers.csv`: `from_crs, to_crs, tfl_min, modes, summary` -- same query rules, every ordered pair of the
31 London hubs in `router.HUBS`: 21 terminals and split codes (PAD, PDX, MYB, EUS, STP, SPL, KGX, LST, LSX, FST, CST, MOG,
CTK, BFR, CHX, WAT, WAE, LBG, VIC, ZFD, FDX) plus 10 interchanges (WEH, WIM, ZWL, TOM, BDS, CLJ, HRW, EPH, HHY, WHP).
Raw TfL duration. STP/SPL, LST/LSX and ZFD/FDX resolve to one TfL station (404 "No journey
found") and are added as 0 min, `walk (same station)` (`tfl_transfers.SAME_STATION`), matching TfL's 0-min walk for PAD/PDX.

`data/results_<CRS>.csv`: `crs, name, lat, lon, depart, arrive, duration_min, n_changes, changes_at,
arrival_station, tfl_min, legs`. `arrive` = arrival at the destination; `arrival_station` = where the rail part ends
(the destination, or the London station the TfL final leg starts from); `tfl_min` = final TfL leg including its
allowance (0 if none). Name and coordinates are those of the TIPLOC the journey starts from.
Timetable TIPLOCs missing from NaPTAN are mapped to a CRS via the MSN file and take that station's NaPTAN name/coordinates.

## Rules

- **Journey chosen**: minimum `arrive - depart` arriving at the destination 08:00-09:00 inclusive; ties -> fewer changes,
  then later departure. Liverpool Street = main line (LST) and Elizabeth line low-level (LSX).
- **Timetable for the date**: permanent schedules with STP overlays/new/cancellations (C > O > N > P); days-run and date range;
  trains from the previous day running past midnight included. Passenger status only; buses, ships, ECS, freight excluded.
- **Stops**: public times only; no boarding at set-down-only stops, no alighting at pick-up-only stops.
- **Changes**: unlimited, counted in `n_changes` and listed in `changes_at`. Changing needs the station's minimum change time
  from the MSN file (default 5 min; max across TIPLOCs of the MSN station). Changes follow MSN stations: NaPTAN CRS codes
  sharing one MSN CRS (e.g. Whitechapel ZWL/WHX, Liverpool Lime Street LIV/LVL, Tamworth TAM/TAH) are linked as
  one station; results stay per NaPTAN CRS. Staying on a train through a join (JJ) or divide (VV)
  is not a change.
- **TfL final leg**: from any Greater London station to a London destination (LST, DMK),
  `arrival + TfL duration + 5 min`; not counted as a change.
- **TfL mid-journey transfer**: between any two of the 31 London hubs, next departure at or after
  `arrival + TfL duration + 10 min` (no station MCT on top); counts as one change, shown in `changes_at` as
  `A -> B (TfL Nm)` and in `legs` as `TfL Nm: <route>` (N includes the allowance). Only at Greater London TIPLOCs.
- **No TfL hop before 05:30** (`router.TFL_FIRST_DEP`): a mid-journey hop needs the train to arrive at 05:30 or later, and a
  journey starting with a hop must start it at 05:30 or later. Only affects Exeter (the 06:00 from Paddington).
- **Starting in London**: a journey from one of the 31 hubs may start with a TfL hop to another hub
  (`TfL duration + 10 min`, first entry in `legs`, not counted as a change); for LST and DMK every London station with a
  TfL route also gets a TfL-only journey (`TfL duration + 5 min`, arriving 09:00, `arrival_station` = the origin).
  Both compete with rail journeys under the usual fastest / fewer changes / later departure rule.
  Other London stations must start with a train from that station (National Rail, Overground, Elizabeth line).
- **Allowances**: TfL's duration runs from the first tube departure, so it excludes walking between mainline and tube
  platforms and waiting. +10 min mid-journey (both ends), +5 min final leg (one end).
- **Map**: destination checkboxes, up to 2 (fixed GB view; a star marks each selected destination; unticking
  the last one empties the map [user]). With two, only stations
  with a result for both are shown, and colour, outline, the duration filter and the hover tooltip use the longer journey
  (equal durations: more changes counts as longer); the popup lists both itineraries, quicker first. Colour = 30-min band (red-amber-green,
  green = shortest), >180 min grey; outline = changes (thickest and darkest = direct, thinnest and palest = 2+); a max-duration filter (slider 30-360 min
  plus typed minutes, which can exceed 360; synced; opens at 360; a "?" tooltip on its header explains the TfL buffers) and an "Include London stations" switch (off by default
  for every destination; London = every TIPLOC of the CRS in Greater London, from `data/stations.csv`); popup shows the
  full itinerary as numbered steps, each with its duration in minutes and, for trains, the operator name
  (`build_map.OPERATORS`; headcodes stay in `legs` only; station codes in rail legs shown as names; codes not in `data/stations.csv` stay as codes). A line at the foot of the panel states that times run from the
  station and getting to it is not included [user].
- **Map page**: one self-contained HTML file loading only Leaflet. Results are embedded as compact JSON (shared station
  table + one row array per destination); markers are drawn on a canvas, created once per station and restyled on
  destination change; popups are built on click.

## Decision log

Each decision is traced from the aim that drove it. `[user]` = your choice; `[Claude]` = default or technical
call made during the build, open to review. Add new entries under the relevant aim; move items from "Open" once decided.

**Overall aim: find places to live outside London with a workable commute to Liverpool Street.**

- **A1. Commute times must reflect real trains**
  - D1 Data source -- options: GB rail timetable (RDG CIF) / journey-planner APIs (Google, TransportAPI, TfL).
    Chose **GB rail timetable** [user]. -> Routing computed locally (`cif.py`, `router.py`); needs Rail Data Marketplace access.
  - D2 Which timetable date -- chose **Tue 2 Feb 2027** [user]: after the 13 Dec 2026 timetable change, i.e. the timetable
    a move would actually commute on. Tuesdays compared against the per-station median across dates: most typical are 24 Nov 2026 (before the change) and
    2 Feb 2027 / 15 Dec 2026 (after). 10 Nov 2026 lacks Chepstow, Lydney and Chandlers Ford (engineering works);
    8 Dec 2026 and 12 Jan 2027 have the most gaps. After the change, ~40 Reading-Waterloo line stations are 17-41 min faster to Oxford.
  - D3 Timetable rules [Claude]: permanent + overlays/cancellations for the date, previous-day trains running past midnight,
    passenger trains only (no rail-replacement buses), public stops only, boarding/alighting restrictions enforced.
  - D4 Change times [Claude]: station minimum change time from the MSN file, default 5 min; staying seated through
    a train joining/dividing is not a change.
    - Split stations -- NaPTAN gives some stations several CRS codes (13 in the MSN, e.g. Whitechapel Overground ZWL /
      Elizabeth line WHX), which blocked changes between them (Surrey Quays -> Oxford took 104 min via London Bridge
      instead of 92 via Whitechapel). Options: link via MSN station codes / add the London pairs as hubs.
      Chose **link via MSN station codes** [user], using the MSN change time.
  - D15 Doubling back -- journeys may travel away from the destination and back to catch a faster train (Salfords ->
    DMK: Thameslink south to Horley, then the fast 1H09 north through Salfords). Valid on the timetable, but the
    National Routeing Guide almost never permits passing through a station twice on one ticket (easements aside).
    A literal check can't see it: fast trains have no CIF timing point at many stations they pass (1H09 records
    HORLEY -> EARLSWD, not Salfords). Proposed alternative: block a rail change where the next train leaves back towards
    the previous train's last stop (angle < 45 degrees between the two at the change station), with per-direction
    frontiers so a reversal can't mask a valid option; misses triangle routes and revisits from earlier legs.
    Chose **leave double-backs allowed for now** [user].
- **A2. Model a weekday commute**
  - D5 Time of day -- chose **weekday morning, arrive Liverpool Street by 09:00** [user].
  - D6 Which journey per station -- options: latest departure arriving by 09:00 / fastest journey / both.
    Chose **fastest** [user], arriving in the window 08:00-09:00 [Claude: window start]. Ties -> fewer changes, then later departure [Claude].
    -> Map shows best-case journey time, not the latest you can leave.
- **A3. Many trains don't go directly to Liverpool Street**
  - D7 Cross-London leg -- chose **TfL transfer** [user].
    - Final leg from any Greater London station (not just termini) [Claude].
    - Mid-journey transfers through London -- chose **yes** [user], between **21 hubs: terminals + same-place split codes**
      (420 TfL queries) [user]; counts as **one change** [user].
    - More hubs (option 1 of: more hubs / every London station to every hub / TfL timetables routed in full) [user].
      Measured on the 2 Feb 2027 results: non-hub London stations called at by trains in journeys using a hub hop
      -> 36 candidates with a TfL route -> each added alone, then greedy forward selection by minutes saved over all
      origins and destinations (London origins included [user]; journeys departing before 06:00 excluded, as the fixed
      09:00-arrival TfL time may not hold before the tube runs [Claude]). Chose **top 10** [user]: West Ham, Wimbledon,
      Whitechapel (Overground code ZWL), Tottenham Hale, Bond Street, Clapham Junction, Harrow & Wealdstone,
      Elephant & Castle, Highbury & Islington, West Hampstead Thameslink -- 972 of the 1,000 min that all 15
      gaining candidates save together; the rest add <=12 min each (Barking adds 0 once West Ham is in).
    - Early-morning hops -- 104 Exeter results used a TfL hop before 05:30 (e.g. Euston 04:37 -> Paddington for the
      06:00), before most tube lines run. Options: block hops before a set time / leave and flag. Chose **block before
      05:30** [user]. Effect on 2 Feb 2027: Exeter 419 -> 337 stations (82 lost, 3 slower by 16 min in total); other
      destinations unchanged.
    - TfL durations exclude platform walks and waiting (found in the hub data, e.g. Euston -> King's Cross = 1 min) ->
      **+10 min mid-journey** [user], **+5 min on the final leg** [user].
    - Same-station split codes TfL can't route (STP/SPL, LST/LSX, ZFD/FDX) -> **0 min + 10 min buffer** [user].
    - Journeys from London origins -- options: hub-start + TfL-only (no new queries) / plus TfL from every London station
      to every hub (~7k queries) / leave rail-only. Chose **hub-start + TfL-only** [user], with non-hub London stations
      flagged as approximate on the map [user]. Start hop not counted as a change [Claude].
      Flag later **dropped** [user]: their only gap (no tube/bus/walk to another station first) is the station-to-station
      scope that applies to every origin (D9), so it is stated once in the map panel instead.
    - Fixed duration from one TfL query per station, arriving by 09:00 on Tue 10 Nov 2026 [Claude].
    - Modes: tube, DLR, Overground, Elizabeth line, walking; **no National Rail, no bus** [Claude] -- rail legs stay timetable-based.
    - Elizabeth line Liverpool Street (LSX) counts as arriving at Liverpool Street [Claude].
    - 196 of 344 Greater London stations have a TfL route under these modes; the rest are reached by train to a station that does.
    - TfL leg applied only at Greater London TIPLOCs [Claude] -- guards against NaPTAN giving Newbury Park the CRS of Newbury Racecourse (NRC).
- **A4. Know how awkward each journey is**
  - D8 Changes -- chose **no limit, but record them** [user]. -> `n_changes`, `changes_at` columns; marker outline on the map.
- **A5. Decide where to look first, then refine to areas**
  - D9 Scope -- chose **station-to-station now, areas (walking access) later** [user]. -> Results kept per station with
    coordinates so an access-time layer can be added without rework.
  - D10 Search radius -- chose **3 hours** as the starting point [user]. -> Colour bands to 180 min; beyond shown grey.
- **A6. Explore the results interactively**
  - D11 Output -- chose **interactive HTML in this folder** [user]. -> Leaflet `map.html` (folium dropped for page size
    and speed [user: "as efficient as possible"]).
- **A7. Compare commutes to other destinations**
  - D13 Destination selection -- options: any station routed in the browser / precomputed list / all pairs.
    Chose **precomputed list** [user]: Liverpool Street, **Denmark Hill, Oxford, Exeter St Davids** [user].
  - D16 Two destinations at once (e.g. two commuters) -- **up to 2, checkboxes** [user]; marker colour and outline from
    the **longer journey** [user]; popup shows **both, quicker first** [user]; stations without a result for both
    destinations **hidden** [user]. Filter and tooltip also follow the longer journey; ties -> more changes = longer [Claude].
  - D14 Map view on switching destination -- **fixed GB view** [user]; London switch default off for all [user].
  - D12 Visual design (under review on `map.html`):
    - Colour: **red-amber-green 30-min bands, green = closest; >180 min grey** [user] (replaced viridis).
    - Basemap: **OpenStreetMap only, no toggle** [user] (CARTO needs a key; Esri Light Gray tried and dropped).
    - **London stations hidden by default**, switch to include them [user] -- the aim is options outside London.
    - **One max-duration filter: slider (capped at 360 min) plus typed minutes**, no per-band layer toggles [user].
    - Outline thickness = changes [Claude]; legend under the duration filter [user]. Outlines not white [user] -- pale
      bands (60-90 min) merged with the OSM basemap. Boldest = direct [user]: direct 5px black, 1 change 3px #555,
      2+ changes 1px #999 (was the reverse).

**Open**
- Planner spot-check of the real run: ~15 stations in `data/results_<CRS>.csv` (each destination) against the National
  Rail journey planner for the same date and arrival window (direct, 1-change, 2+-change and TfL-leg cases), and confirm
  known stations aren't missing. Not yet done; it is the only check on the CIF code tables (see Limitations).
- Area layer method: walking only vs walk/drive; straight-line with detour factor vs road routing.

## Limitations

- Scheduled times only: no delays, cancellations or engineering works.
- Fares and ticket routeing are not modelled: a journey may double back through a station (D15), which a through
  ticket usually wouldn't allow.
- TfL transfer times are fixed (one 09:00-arrival query per station or hub pair), not time-dependent; the +5/+10 min
  allowances are flat, not station-specific. The 05:30 earliest hop is one cut-off for every line (first trains vary
  by line and station).
- Mid-journey TfL transfers only between the 31 hubs; other London stations connect by rail only (plus the final leg).
- Journeys from non-hub London stations can't start by tube/bus/walk to another station, so their times may be
  overstated (as for any origin, where driving to a better-served station isn't modelled either).
- A journey from one CRS of a split station can't start with a train from its sibling CRS (e.g. a Whitechapel WHX
  origin can't start on the Overground from ZWL); each NaPTAN CRS keeps its own result row.
- CIF status/category/activity code tables were taken from the CIF v29 spec as the 2025 spec's tables did not extract;
  to be checked against the real feed.
- Greater London membership is by NPTG locality, not a boundary polygon.
- 11 Elizabeth line stations had no NaPTAN lat/lon; coordinates converted from OSGB easting/northing (accurate to a few metres).
