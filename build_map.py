import argparse
import json
from pathlib import Path

import pandas as pd

from router import DESTINATIONS, FINAL_ALLOWANCE, HUB_ALLOWANCE

BANDS = [(0, 30), (30, 60), (60, 90), (90, 120), (120, 150), (150, 180)]
COLOURS = ["#1a9850", "#91cf60", "#d9ef8b", "#fee08b", "#fc8d59", "#d73027"]  # RdYlGn: green = closest
OVER_COLOUR = "#9e9e9e"
OUTLINE = {0: (5, "#000000"), 1: (3, "#555555"), 2: (1, "#999999")}  # boldest = direct
SLIDER_MAX = 360  # typed box can go higher
LEG_CODES = r"(?:^|\| )(\S+) \d\d:\d\d -> (\S+) \d\d:\d\d"  # station codes in router legs
OPERATORS = {  # CIF ATOC code -> public name; codes not listed are shown as-is
    "AW": "Transport for Wales", "CC": "c2c", "CH": "Chiltern Railways", "EM": "East Midlands Railway",
    "GN": "Great Northern", "GR": "LNER", "GW": "GWR", "GX": "Gatwick Express",
    "HX": "Heathrow Express", "LE": "Greater Anglia", "LM": "West Midlands Trains", "LO": "London Overground",
    "LT": "London Underground", "ME": "Merseyrail", "NT": "Northern", "SE": "Southeastern", "SN": "Southern",
    "SW": "South Western Railway", "TL": "Thameslink", "TP": "TransPennine Express", "VT": "Avanti West Coast",
    "XC": "CrossCountry", "XR": "Elizabeth line",
}

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rail commute times</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>
html,body,#map{height:100%;margin:0}
.tp{background:#fff;border-radius:12px;box-shadow:0 4px 18px rgba(15,23,42,.15);padding:14px 16px;width:230px;
  font:13px/1.4 system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;color:#1e293b}
.tp h4{margin:0 0 6px;font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:#64748b}
.tp .dst{margin-bottom:12px}
.tp .dst label{display:flex;align-items:center;gap:6px;cursor:pointer;font-weight:600;color:#0f172a}
.tp .dst input{margin:0;accent-color:#3b82f6}
.tp .dst input:disabled:not(:checked)+span{color:#94a3b8}
.tp .dst .note{margin-top:4px}
.tp .val{display:flex;align-items:baseline;gap:4px;margin-bottom:6px}
.tp .val input{width:64px;font:600 22px system-ui,sans-serif;color:#0f172a;border:1px solid #e2e8f0;border-radius:8px;
  padding:2px 8px;outline:none;-moz-appearance:textfield}
.tp .val input:focus{border-color:#3b82f6;box-shadow:0 0 0 3px rgba(59,130,246,.2);outline:none}
.tp .val input::-webkit-inner-spin-button{-webkit-appearance:none}
.tp .val span{color:#64748b}
.tp input[type=range]{-webkit-appearance:none;appearance:none;width:100%;height:6px;border-radius:3px;margin:6px 0 12px}
.tp input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:18px;height:18px;border-radius:50%;background:#fff;
  border:2px solid #0f172a;box-shadow:0 1px 4px rgba(0,0,0,.25);cursor:pointer}
.tp input[type=range]::-moz-range-thumb{width:14px;height:14px;border-radius:50%;background:#fff;border:2px solid #0f172a;cursor:pointer}
.tp .sw{display:flex;align-items:center;justify-content:space-between;cursor:pointer;user-select:none}
.tp .sw input{display:none}
.tp .sw i{position:relative;width:34px;height:20px;border-radius:10px;background:#cbd5e1;transition:.2s}
.tp .sw i:after{content:'';position:absolute;top:2px;left:2px;width:16px;height:16px;border-radius:50%;background:#fff;
  box-shadow:0 1px 2px rgba(0,0,0,.3);transition:.2s}
.tp .sw input:checked+i{background:#3b82f6}
.tp .sw input:checked+i:after{left:16px}
.tp hr{border:0;border-top:1px solid #e2e8f0;margin:12px 0}
.tp hr.key{border-top:3px solid #cbd5e1;margin:14px 0}
.tp .grid{display:grid;grid-template-columns:1fr 1fr;gap:4px 10px;margin-bottom:10px}
.tp .dot{display:inline-block;width:12px;height:12px;border-radius:50%;vertical-align:-1px;margin-right:6px;box-sizing:border-box}
.tp h4{position:relative}
.tp .note{margin:0;font-size:11px;color:#64748b}
.tp .info{display:inline-block;width:14px;height:14px;margin-left:4px;border-radius:50%;background:#e2e8f0;
  color:#475569;font-size:10px;line-height:14px;text-align:center;letter-spacing:0;cursor:help;vertical-align:1px;outline:none}
.tp .info .tt{display:none;position:absolute;left:0;right:0;top:20px;padding:8px 10px;border-radius:8px;background:#0f172a;
  color:#f1f5f9;font:400 12px/1.4 system-ui,sans-serif;text-transform:none;text-align:left;z-index:1000}
.tp .info:hover .tt,.tp .info:focus .tt{display:block}
.pop{font:12px sans-serif;min-width:260px}.pop hr{margin:4px 0}.pop .legs{font:11px monospace}
.pop hr.sep{margin:8px 0;border:0;border-top:2px solid #94a3b8}
</style></head><body><div id="map"></div>
<script>
const D = __DATA__;
const S = D.stations, OUT = D.outline;
const pad = n => String(n).padStart(2, '0');
const hhmm = m => (m = (m % 1440 + 1440) % 1440, pad(m / 60 | 0) + ':' + pad(m % 60));
const hm = m => m >= 60 ? (m / 60 | 0) + 'h' + pad(m % 60) : m + ' min';
const esc = s => String(s).replace(/[&<>"']/g, c => '&#' + c.charCodeAt(0) + ';');
const mins = t => +t.slice(0, 2) * 60 + +t.slice(3);
const LEG = /^(\S+) (\d\d:\d\d) -> (\S+) (\d\d:\d\d) \[(\S+) \S+\]$/;  // rail leg: codes -> names, headcode -> duration
const TFL_LEG = /^TfL (\d+)m: (.*)$/;

const map = L.map('map', {preferCanvas: true, center: [52.3, -1.2], zoom: 7});
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  {maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}).addTo(map);
const group = L.featureGroup().addTo(map);
const markers = S.lat.map((lat, i) => Object.assign(L.circleMarker([lat, S.lon[i]]), {i: i, shown: false}));
const starIcon = L.divIcon({className: '', iconSize: [24, 24], iconAnchor: [12, 12],
  html: '<svg width="24" height="24" viewBox="0 0 24 24"><path d="M12 2l3 7h7l-5.5 4.5 2 7.5-6.5-4.5-6.5 4.5 2-7.5L2 9h7z" fill="#000" stroke="#fff" stroke-width="1.5"/></svg>'});
Object.values(D.dests).forEach(d => {
  d.star = L.marker(d.latlng, {zIndexOffset: 1000, icon: starIcon}).bindTooltip(esc(d.name));
  d.byStation = [];
  d.rows.forEach(r => d.byStation[r[0]] = r);
});
let routes = [];  // per station: [[destKey, row], ...] quickest first; empty slot if any selected destination has no route
const longest = i => routes[i].at(-1)[1];

function style(r) {
  const b = D.bands.findIndex(hi => r[1] < hi), over = b < 0, o = OUT[Math.min(r[2], 2)];
  return {radius: over ? 4 : 8, weight: o[0], color: o[1], fillColor: over ? D.over : D.colours[b],
          fillOpacity: over ? 0.5 : 0.9, opacity: over ? 0.6 : 1};
}

function popup(i) {
  return '<div class="pop"><b>' + esc(S.name[i]) + ' (' + S.crs[i] + ')</b><br>'
    + routes[i].map(([k, r]) => route(D.dests[k], r)).join('<hr class="sep">') + '</div>';
}

function route(dest, [, dur, nch, dep, tfl, at, arr, legs]) {
  return 'Duration to ' + esc(dest.name) + ': <b>' + hm(dur) + '</b> (' + dur + ' min)<br>'
    + hhmm(dep) + ' -> ' + hhmm(dep + dur) + '<br>Changes: ' + nch + (at ? ' (' + esc(at) + ')' : '') + '<br>'
    + 'Arrival station: ' + esc(D.strings[arr]) + '; ' + (tfl ? tfl + ' min TfL transfer' : 'no TfL leg')
    + '<hr><span class="legs">' + legs.split('|').map((l, k) => (k + 1) + '. ' + esc(l.trim()
        .replace(LEG, (m, a, t1, b, t2, op) => (D.names[a] || a) + ' ' + t1 + ' -> ' + (D.names[b] || b) + ' ' + t2
          + ' [' + (D.operators[op] || op) + ', ' + (mins(t2) - mins(t1) + 1440) % 1440 + ' min]')
        .replace(TFL_LEG, '$2 [TfL, $1 min]'))).join('<br>') + '</span>';
}

group.on('click', e => L.popup({maxWidth: Math.min(720, innerWidth - 60)}).setLatLng(e.layer.getLatLng()).setContent(popup(e.layer.i)).openOn(map));
const tip = L.tooltip({direction: 'top', offset: [0, -8]});
group.on('mouseover', e => map.openTooltip(tip.setContent(esc(S.name[e.layer.i]) + ': ' + hm(longest(e.layer.i)[1])), e.layer.getLatLng()));
group.on('mouseout', () => map.closeTooltip(tip));

const ctl = L.control({position: 'topright'});
ctl.onAdd = () => {
  const d = L.DomUtil.create('div', 'tp');
  d.innerHTML = '<h4>Destinations (up to 2)</h4><div class="dst">'
    + Object.keys(D.dests).map((k, n) => '<label><input type="checkbox" value="' + k + '"' + (n ? '' : ' checked') + '><span>'
      + esc(D.dests[k].name) + '</span></label>').join('')
    + '<p class="note">Colour, outline and the time displays reflect the longer journey.</p></div>'
    + '<h4>Max journey time<span class="info" tabindex="0">?<span class="tt">' + D.bufferNote + '</span></span></h4>'
    + '<div class="val"><input id="thrNum" type="number" min="1" value="' + D.sliderMax + '"><span>min</span></div>'
    + '<input id="thr" type="range" min="30" max="' + D.sliderMax + '" step="5" value="' + D.sliderMax + '" style="background:linear-gradient(90deg,' + D.track + ')">'
    + '<label class="sw">Include London stations<input id="ldn" type="checkbox"><i></i></label>' + D.legend
    + '<p class="note">Journey times start directly from stations and do not include travel to the start.</p>';
  L.DomEvent.disableClickPropagation(d);
  L.DomEvent.disableScrollPropagation(d);
  return d;
};
ctl.addTo(map);
const boxes = [...document.querySelectorAll('.dst input')], thr = document.getElementById('thr'),
      thrNum = document.getElementById('thrNum'), ldn = document.getElementById('ldn');

function apply() {
  const t = +thrNum.value || D.sliderMax;
  markers.forEach((m, i) => {
    const show = !!routes[i] && longest(i)[1] <= t && (ldn.checked || !S.ldn[i]);
    if (show !== m.shown) { m.shown = show; show ? group.addLayer(m) : group.removeLayer(m); }
  });
}

function setDests() {
  const ks = boxes.filter(b => b.checked).map(b => b.value);
  boxes.forEach(b => b.disabled = !b.checked && ks.length === 2);
  Object.entries(D.dests).forEach(([k, d]) => ks.includes(k) ? d.star.addTo(map) : d.star.remove());
  routes = [];
  (ks.length ? D.dests[ks[0]].rows : []).forEach(([i]) => {
    const rs = ks.map(k => [k, D.dests[k].byStation[i]]);
    if (rs.every(([, r]) => r)) {
      routes[i] = rs.sort((a, b) => a[1][1] - b[1][1] || a[1][2] - b[1][2]);  // ties: more changes counts as longer
      markers[i].setStyle(style(longest(i)));
    }
  });
  if (ks.length) thrNum.max = Math.ceil(Math.max(...routes.filter(Boolean).map(r => r.at(-1)[1][1])) / 30) * 30;
  map.closePopup();
  apply();
}

boxes.forEach(b => b.addEventListener('change', setDests));
thr.addEventListener('input', () => { thrNum.value = thr.value; apply(); });
thrNum.addEventListener('input', () => { if (thrNum.value !== '') { thr.value = thrNum.value; apply(); } });
ldn.addEventListener('change', apply);
setDests();
</script></body></html>
"""


def legend_html():
    rows = [(COLOURS[i], f"{lo}-{hi} min") for i, (lo, hi) in enumerate(BANDS)] + [(OVER_COLOUR, "180+ min")]
    swatches = "".join(f"<div><span class='dot' style='background:{c}'></span>{t}</div>" for c, t in rows)
    outlines = "".join(
        f"<div><span class='dot' style='background:#e2e8f0;border:{w}px solid {c}'></span>{t}</div>"
        for (w, c), t in zip(OUTLINE.values(), ["Direct", "1 change", "2+ changes"])
    )
    return f"<hr class='key'><h4>Journey time</h4><div class='grid'>{swatches}</div><h4>Changes (outline)</h4><div class='grid'>{outlines}</div>"


def minutes(hhmm):
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", default="data", help="folder holding results_<CRS>.csv")
    ap.add_argument("--out", default="map.html")
    ap.add_argument("--stations", default="data/stations.csv")
    args = ap.parse_args()

    stations = pd.read_csv(args.stations)
    first_tiploc = stations.drop_duplicates("crs").set_index("crs")
    # all(): a CRS is London only if every TIPLOC is (NRC is shared by Newbury Park and Newbury Racecourse)
    in_london = stations.groupby("crs").in_london.all()
    results = {d: pd.read_csv(p, keep_default_na=False)
               for d in DESTINATIONS if (p := Path(args.results_dir) / f"results_{d}.csv").exists()}
    origins = pd.concat(results.values()).drop_duplicates("crs").sort_values("crs", ignore_index=True)
    index = dict(zip(origins.crs, origins.index))
    strings = sorted(set().union(*(df.arrival_station for df in results.values())))
    leg_codes = set(pd.concat(df.legs for df in results.values()).str.extractall(LEG_CODES).stack())
    string_index = {s: i for i, s in enumerate(strings)}

    pct = lambda m: f"{max(0, (m - 30) / (SLIDER_MAX - 30) * 100):.1f}%"
    data = {
        "stations": {"crs": origins.crs.tolist(), "name": origins.name.tolist(),
                     "lat": origins.lat.round(5).tolist(), "lon": origins.lon.round(5).tolist(),
                     "ldn": origins.crs.map(in_london).fillna(False).astype(int).tolist()},
        "dests": {d: {"name": first_tiploc.name[d], "latlng": [round(first_tiploc.lat[d], 5), round(first_tiploc.lon[d], 5)],
                      "rows": [[index[r.crs], int(r.duration_min), int(r.n_changes), minutes(r.depart), int(r.tfl_min),
                                r.changes_at, string_index[r.arrival_station], r.legs] for r in df.itertuples()]}
                  for d, df in results.items()},
        "strings": strings,
        "names": {c: first_tiploc.name[c] for c in sorted(leg_codes) if c in first_tiploc.index},
        "operators": OPERATORS,
        "bands": [hi for _, hi in BANDS],
        "colours": COLOURS,
        "over": OVER_COLOUR,
        "outline": [list(OUTLINE[k]) for k in sorted(OUTLINE)],
        "sliderMax": SLIDER_MAX,
        "track": ",".join(f"{c} {pct(lo)} {pct(hi)}" for c, (lo, hi) in zip(COLOURS, BANDS)) + f",{OVER_COLOUR} {pct(BANDS[-1][1])}",
        "legend": legend_html(),
        "bufferNote": (f"TfL legs include a buffer for walking between mainline and tube platforms and waiting: "
                       f"+{FINAL_ALLOWANCE} min on a final TfL leg to the destination, "
                       f"+{HUB_ALLOWANCE} min on a TfL transfer between London terminals mid-journey."),
    }
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    Path(args.out).write_text(PAGE.replace("__DATA__", payload), encoding="utf-8")
    print(f"{', '.join(f'{d}: {len(df)}' for d, df in results.items())} stations -> {args.out}")


if __name__ == "__main__":
    main()
