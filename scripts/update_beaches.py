#!/usr/bin/env python3
"""Daily bake: MG + AEMET + Copernicus → data/index.json + data/<concello>.json."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog.json"
DATA_DIR = ROOT / "data"
BEACHES_LEGACY = ROOT / "beaches.json"

MG_URL = "https://servizos.meteogalicia.gal/mgrss/predicion/jsonPredPraia.action?idPraia={}"
AEMET_URL = "https://opendata.aemet.es/opendata/api/prediccion/especifica/playa/{}"

CMEMS_DATASET = "cmems_mod_ibi_phy_anfc_0.027deg-2D_PT1H-m"
CMEMS_VAR = "thetao"
GAL_LAT = (41.7, 43.9)
GAL_LON = (-9.4, -6.7)
CMEMS_SEARCH_RADIUS = 2  # cells
HISTORY_DAYS = 7
PRIMARY_ORDER = ("MeteoGalicia", "AEMET")
TOP_CAP = 20
TOP_PER_CONCELLO = 2


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def http_get(url: str, headers: dict | None = None, timeout: int = 45) -> tuple[bytes, dict]:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        hdrs = {k.lower(): v for k, v in resp.headers.items()}
    return body, hdrs


def _round_t(t: Any) -> float:
    return round(float(t), 1)


def day_for_date(days: list[dict], day: str) -> dict | None:
    for d in days or []:
        if d.get("date") == day and isinstance(d.get("t"), (int, float)):
            return d
    return None


def fetch_mg(beach_id: int) -> list[dict] | None:
    url = MG_URL.format(beach_id)
    for attempt in range(3):
        try:
            body, _ = http_get(url)
            data = json.loads(body.decode("utf-8"))
            days = data["predPraia"]["listaPredDiaPraia"]
            out = []
            for d in days[:3]:
                t = d.get("tAuga")
                if t is None or t == -9999:
                    continue
                raw = d["dataPredicion"]
                day = raw[:10] if isinstance(raw, str) else str(raw)[:10]
                out.append({"date": day, "t": _round_t(t)})
            if not out:
                return None
            return out
        except Exception:  # noqa: BLE001
            if attempt == 2:
                return None
            time.sleep(1.0 + attempt)
    return None


def fetch_aemet(aemet_id: str, api_key: str) -> list[dict] | None:
    headers = {"api_key": api_key, "Accept": "application/json"}
    url = AEMET_URL.format(aemet_id)
    remaining_pause = 2.0

    for attempt in range(3):
        try:
            body, hdrs = http_get(url, headers=headers)
            rem = hdrs.get("remaining-request-endpoint")
            if rem is not None:
                try:
                    if int(rem) < 5:
                        time.sleep(15)
                except ValueError:
                    pass
            if not body.strip():
                return None
            meta = json.loads(body.decode("utf-8"))
            if meta.get("estado") != 200 or not meta.get("datos"):
                return None
            time.sleep(remaining_pause)
            body2, hdrs2 = http_get(meta["datos"], headers=headers)
            rem2 = hdrs2.get("remaining-request-endpoint")
            if rem2 is not None:
                try:
                    if int(rem2) < 5:
                        time.sleep(15)
                except ValueError:
                    pass
            if not body2.strip():
                return None
            payload = json.loads(body2.decode("iso-8859-1"))
            if isinstance(payload, list):
                payload = payload[0]
            dias = payload["prediccion"]["dia"]
            out = []
            for d in dias[:3]:
                fecha = str(d["fecha"])
                if len(fecha) == 8 and fecha.isdigit():
                    day = f"{fecha[:4]}-{fecha[4:6]}-{fecha[6:8]}"
                else:
                    day = fecha[:10]
                t = d["tAgua"]["valor1"]
                if t is None:
                    continue
                out.append({"date": day, "t": _round_t(t)})
            if not out:
                return None
            return out
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                remaining_pause = min(remaining_pause * 2, 30)
                time.sleep(remaining_pause)
                continue
            if attempt == 2:
                return None
            time.sleep(1.0 + attempt)
        except Exception:  # noqa: BLE001
            if attempt == 2:
                return None
            time.sleep(1.0 + attempt)
    return None


def load_previous_state() -> dict[int, dict]:
    """beach_id → previous beach record (from data/*.json or legacy beaches.json)."""
    by_id: dict[int, dict] = {}
    if DATA_DIR.is_dir():
        for path in DATA_DIR.glob("*.json"):
            if path.name == "index.json":
                continue
            payload = load_json(path)
            for b in payload.get("beaches", []):
                if "id" in b:
                    by_id[b["id"]] = b
    if by_id:
        return by_id
    legacy = load_json(BEACHES_LEGACY)
    for b in legacy.get("beaches", []):
        if "id" in b:
            by_id[b["id"]] = b
    return by_id


def previous_source(prev_beach: dict | None, source_name: str) -> dict | None:
    if not prev_beach:
        return None
    for s in prev_beach.get("sources", []):
        if s.get("name") == source_name and s.get("days"):
            return s
    return None


def source_fresh(src: dict, max_age_days: int = 3) -> bool:
    try:
        dates = [d.get("date") for d in src.get("days") or [] if d.get("date")]
        if not dates:
            return False
        d0 = date.fromisoformat(min(dates))
    except (KeyError, IndexError, ValueError, TypeError):
        return False
    return (today_utc() - d0).days <= max_age_days


def pick_primary(sources: list[dict], today: str) -> tuple[float | None, str | None]:
    """Return (t, source_name) for today's primary figure only."""
    by_name = {s["name"]: s for s in sources if s.get("days")}
    for name in PRIMARY_ORDER:
        s = by_name.get(name)
        if not s:
            continue
        d = day_for_date(s["days"], today)
        if d is not None:
            return float(d["t"]), name
    for s in sources:
        d = day_for_date(s.get("days") or [], today)
        if d is not None:
            return float(d["t"]), s["name"]
    return None, None


def compute_trend(history: list[dict], today_t: float, today: str) -> str | None:
    target = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
    past = next((h for h in history if h.get("date") == target), None)
    if past is None or not isinstance(past.get("t"), (int, float)):
        return None
    delta = round(float(today_t) - float(past["t"]), 1)
    if delta >= 0.5:
        return "up"
    if delta <= -0.5:
        return "down"
    return "flat"


def merge_history(
    old: list[dict] | None,
    today_point: dict,
    *,
    prev_day0: dict | None = None,
    replace: list[dict] | None = None,
    keep_days: int = HISTORY_DAYS,
) -> list[dict]:
    """Keep up to keep_days past points (dates strictly before today_point)."""
    if replace is not None:
        pts = {
            h["date"]: _round_t(h["t"])
            for h in replace
            if "date" in h and "t" in h
        }
    else:
        pts = {
            h["date"]: _round_t(h["t"])
            for h in (old or [])
            if "date" in h and "t" in h
        }
        if prev_day0 and prev_day0.get("date") and prev_day0.get("t") is not None:
            if prev_day0["date"] < today_point["date"]:
                pts[prev_day0["date"]] = _round_t(prev_day0["t"])
    today = date.fromisoformat(today_point["date"])
    out = []
    for i in range(1, keep_days + 1):
        d = (today - timedelta(days=i)).isoformat()
        if d in pts:
            out.append({"date": d, "t": pts[d]})
    out.sort(key=lambda x: x["date"])
    return out


def nearest_wet_cell(
    field_2d: Any,
    lats: Any,
    lons: Any,
    lat: float,
    lon: float,
    radius: int = CMEMS_SEARCH_RADIUS,
) -> float | None:
    """field_2d[lat_i, lon_j]; return nearest non-NaN within ±radius cells."""
    lat_list = list(lats)
    lon_list = list(lons)
    i = min(range(len(lat_list)), key=lambda k: abs(float(lat_list[k]) - lat))
    j = min(range(len(lon_list)), key=lambda k: abs(float(lon_list[k]) - lon))
    best = None
    best_d2 = None
    n_i, n_j = len(lat_list), len(lon_list)
    for di in range(-radius, radius + 1):
        for dj in range(-radius, radius + 1):
            ii, jj = i + di, j + dj
            if ii < 0 or jj < 0 or ii >= n_i or jj >= n_j:
                continue
            val = field_2d[ii][jj] if not hasattr(field_2d, "shape") else field_2d[ii, jj]
            try:
                fv = float(val)
            except (TypeError, ValueError):
                continue
            if math.isnan(fv):
                continue
            d2 = di * di + dj * dj
            if best_d2 is None or d2 < best_d2:
                best_d2 = d2
                best = fv
    return best


def fetch_copernicus_grid(
    today: date,
) -> tuple[dict[str, Any], list[float], list[float]] | None:
    """
    One Galicia subset. Returns (daily_means[date_iso] = 2d array, lats, lons)
    or None on failure.
    """
    try:
        import copernicusmarine
        import numpy as np
        import xarray as xr  # noqa: F401
    except ImportError as exc:
        print(f"copernicusmarine unavailable: {exc}", file=sys.stderr)
        return None

    start = today - timedelta(days=HISTORY_DAYS)
    end = today + timedelta(days=2)
    try:
        ds = copernicusmarine.open_dataset(
            dataset_id=CMEMS_DATASET,
            variables=[CMEMS_VAR],
            minimum_longitude=GAL_LON[0],
            maximum_longitude=GAL_LON[1],
            minimum_latitude=GAL_LAT[0],
            maximum_latitude=GAL_LAT[1],
            start_datetime=start.isoformat(),
            end_datetime=(end + timedelta(days=1)).isoformat(),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Copernicus subset failed: {exc}", file=sys.stderr)
        return None

    try:
        da = ds[CMEMS_VAR]
        # drop depth if present
        if "depth" in da.dims:
            da = da.isel(depth=0)
        # daily mean over UTC calendar day
        daily = da.resample(time="1D").mean()
        daily = daily.load()
        lats = daily["latitude"].values
        lons = daily["longitude"].values
        out: dict[str, Any] = {}
        for tval in daily["time"].values:
            # numpy datetime64 → date
            ts = np.datetime64(tval, "D")
            day = date(int(str(ts)[:4]), int(str(ts)[5:7]), int(str(ts)[8:10]))
            out[day.isoformat()] = daily.sel(time=tval).values
        return out, lats, lons
    except Exception as exc:  # noqa: BLE001
        print(f"Copernicus process failed: {exc}", file=sys.stderr)
        return None
    finally:
        try:
            ds.close()
        except Exception:  # noqa: BLE001
            pass


def extract_copernicus_for_beach(
    grid: dict[str, Any],
    lats: Any,
    lons: Any,
    lat: float,
    lon: float,
    today: str,
) -> dict | None:
    """Build Copernicus source dict with days + history from grid."""
    today_field = grid.get(today)
    if today_field is None:
        return None
    t0 = nearest_wet_cell(today_field, lats, lons, lat, lon)
    if t0 is None:
        return None
    days = [{"date": today, "t": _round_t(t0)}]
    # forecast days if present
    tdate = date.fromisoformat(today)
    for add in (1, 2):
        d = (tdate + timedelta(days=add)).isoformat()
        field = grid.get(d)
        if field is None:
            continue
        tv = nearest_wet_cell(field, lats, lons, lat, lon)
        if tv is None:
            continue
        days.append({"date": d, "t": _round_t(tv)})

    history = []
    for i in range(1, HISTORY_DAYS + 1):
        d = (tdate - timedelta(days=i)).isoformat()
        field = grid.get(d)
        if field is None:
            continue
        tv = nearest_wet_cell(field, lats, lons, lat, lon)
        if tv is None:
            continue
        history.append({"date": d, "t": _round_t(tv)})
    history.sort(key=lambda x: x["date"])
    return {"name": "Copernicus", "days": days, "history": history}


def copernicus_sane(today_temps: list[float], n_beaches: int) -> bool:
    if not today_temps:
        return False
    if len(today_temps) < n_beaches / 2:
        print(
            f"Copernicus sanity: only {len(today_temps)}/{n_beaches} today values",
            file=sys.stderr,
        )
        return False
    med = sorted(today_temps)[len(today_temps) // 2]
    if med < 8 or med > 26:
        print(f"Copernicus sanity: median {med}°C outside 8–26", file=sys.stderr)
        return False
    return True


def attach_primary_fields(beach: dict, today: str) -> None:
    sources = beach.get("sources") or []
    t, src = pick_primary(sources, today)
    beach["t"] = t
    beach["source"] = src
    if t is None or src is None:
        beach["trend"] = None
        return

    primary = next(s for s in sources if s["name"] == src)
    hist = primary.get("history") or []
    beach["trend"] = compute_trend(hist, t, today)


def build_top(beaches: list[dict], cap: int = TOP_CAP) -> list[dict]:
    """Warmest beaches: sort by t desc, at most TOP_PER_CONCELLO per concello, cap total."""
    ranked = [
        b
        for b in beaches
        if isinstance(b.get("t"), (int, float))
    ]
    ranked.sort(
        key=lambda b: (-float(b["t"]), b["name"].casefold(), b["slug"])
    )
    per: dict[str, int] = defaultdict(int)
    out: list[dict] = []
    for b in ranked:
        slug = b["concelloSlug"]
        if per[slug] >= TOP_PER_CONCELLO:
            continue
        per[slug] += 1
        sources_slim = []
        for s in b.get("sources") or []:
            sources_slim.append(
                {"name": s["name"], "days": s.get("days") or []}
            )
        out.append(
            {
                "slug": b["slug"],
                "name": b["name"],
                "concello": b["concello"],
                "concelloSlug": b["concelloSlug"],
                "t": b["t"],
                "source": b.get("source"),
                "trend": b.get("trend"),
                "sources": sources_slim,
            }
        )
        if len(out) >= cap:
            break
    return out


def write_data_split(fetched_at: str, beaches: list[dict]) -> None:
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    DATA_DIR.mkdir(parents=True)

    by_concello: dict[str, list[dict]] = defaultdict(list)
    concello_names: dict[str, str] = {}
    beach_map: dict[str, str] = {}

    for b in beaches:
        slug = b["concelloSlug"]
        concello_names[slug] = b["concello"]
        beach_map[b["slug"]] = slug
        by_concello[slug].append(
            {
                "id": b["id"],
                "slug": b["slug"],
                "name": b["name"],
                "t": b.get("t"),
                "source": b.get("source"),
                "trend": b.get("trend"),
                "sources": b.get("sources") or [],
            }
        )

    concellos = [
        {"slug": s, "name": concello_names[s]}
        for s in sorted(concello_names, key=lambda x: concello_names[x].casefold())
    ]
    index = {
        "fetchedAt": fetched_at,
        "concellos": concellos,
        "beachConcello": beach_map,
        "top": build_top(beaches),
    }
    (DATA_DIR / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for slug, items in by_concello.items():
        payload = {"fetchedAt": fetched_at, "beaches": items}
        (DATA_DIR / f"{slug}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    if BEACHES_LEGACY.exists():
        BEACHES_LEGACY.unlink()
        print("removed beaches.json")


def run_selfcheck() -> int:
    today = "2026-08-18"
    yesterday = "2026-08-17"
    tomorrow = "2026-08-19"

    # primary: MG wins over Copernicus
    sources = [
        {"name": "MeteoGalicia", "days": [{"date": today, "t": 14.0}]},
        {"name": "Copernicus", "days": [{"date": today, "t": 16.0}], "history": []},
    ]
    t, src = pick_primary(sources, today)
    assert (t, src) == (14.0, "MeteoGalicia"), (t, src)

    beach = {
        "sources": [
            {
                "name": "MeteoGalicia",
                "days": [{"date": today, "t": 14.0}],
                "history": [{"date": yesterday, "t": 13.0}],
            },
            {
                "name": "Copernicus",
                "days": [{"date": today, "t": 16.0}],
                "history": [{"date": yesterday, "t": 15.0}],
            },
        ]
    }
    attach_primary_fields(beach, today)
    assert beach["source"] == "MeteoGalicia" and beach["t"] == 14.0
    by_name = {s["name"]: s for s in beach["sources"]}
    assert by_name["MeteoGalicia"]["history"] == [{"date": yesterday, "t": 13.0}]
    assert by_name["Copernicus"]["history"] == [{"date": yesterday, "t": 15.0}]

    # only MG
    t, src = pick_primary(
        [{"name": "MeteoGalicia", "days": [{"date": today, "t": 14.0}]}], today
    )
    assert (t, src) == (14.0, "MeteoGalicia")

    # nothing
    t, src = pick_primary([], today)
    assert (t, src) == (None, None)

    # yesterday only → no primary
    t, src = pick_primary(
        [{"name": "MeteoGalicia", "days": [{"date": yesterday, "t": 14.0}]}], today
    )
    assert (t, src) == (None, None)

    # today not at index 0
    t, src = pick_primary(
        [
            {
                "name": "MeteoGalicia",
                "days": [
                    {"date": tomorrow, "t": 20.0},
                    {"date": today, "t": 15.5},
                ],
            }
        ],
        today,
    )
    assert (t, src) == (15.5, "MeteoGalicia"), (t, src)

    # trend vs yesterday, ±0.5 with round(delta, 1)
    hist = [{"date": yesterday, "t": 14.0}]
    assert compute_trend(hist, 14.4, today) == "flat"
    assert compute_trend(hist, 14.5, today) == "up"
    assert compute_trend(hist, 13.5, today) == "down"
    assert compute_trend(hist, 13.6, today) == "flat"
    # float noise: 18.3 - 17.8
    assert compute_trend([{"date": yesterday, "t": 17.8}], 18.3, today) == "up"
    assert compute_trend([], 14.0, today) is None
    assert compute_trend([{"date": "2026-08-11", "t": 14.0}], 15.0, today) is None

    # nearest wet cell ±2
    lats = [42.0, 42.03, 42.06, 42.09, 42.12]
    lons = [-8.9, -8.87, -8.84, -8.81, -8.78]
    field = [[float("nan")] * 5 for _ in range(5)]
    field[2][2] = 15.4
    v = nearest_wet_cell(field, lats, lons, 42.06, -8.84)
    assert v == 15.4
    field2 = [[float("nan")] * 5 for _ in range(5)]
    field2[0][0] = 10.0
    v2 = nearest_wet_cell(field2, lats, lons, 42.06, -8.84, radius=1)
    assert v2 is None
    v3 = nearest_wet_cell(field2, lats, lons, 42.06, -8.84, radius=2)
    assert v3 == 10.0

    # history trim keeps float
    old = [{"date": f"2026-08-{d:02d}", "t": 18.4} for d in range(1, 18)]
    merged = merge_history(old, {"date": today, "t": 15.0})
    assert len(merged) <= HISTORY_DAYS
    assert all(h["date"] < today for h in merged)
    assert all(h["t"] == 18.4 for h in merged)

    # top: ≤2 per concello
    sample = [
        {
            "slug": f"a-{i}",
            "name": f"A{i}",
            "concello": "Hot",
            "concelloSlug": "hot",
            "t": 25.0 - i * 0.1,
            "source": "Copernicus",
            "trend": None,
            "sources": [{"name": "Copernicus", "days": [{"date": today, "t": 25.0}]}],
        }
        for i in range(3)
    ] + [
        {
            "slug": "b-1",
            "name": "B1",
            "concello": "Cool",
            "concelloSlug": "cool",
            "t": 20.0,
            "source": "Copernicus",
            "trend": None,
            "sources": [{"name": "Copernicus", "days": [{"date": today, "t": 20.0}]}],
        }
    ]
    top = build_top(sample, cap=20)
    hot = [x for x in top if x["concelloSlug"] == "hot"]
    assert len(hot) == 2, hot
    assert hot[0]["t"] >= hot[1]["t"]

    print("selfcheck OK")
    return 0


def _mg_has_today(days: list[dict] | None, today: str) -> bool:
    return bool(days and day_for_date(days, today))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="First N catalog beaches (dry-run)")
    parser.add_argument("--selfcheck", action="store_true")
    parser.add_argument("--skip-mg", action="store_true", help="Skip MG fetch (dev)")
    parser.add_argument("--skip-copernicus", action="store_true")
    args = parser.parse_args(argv)

    if args.selfcheck:
        return run_selfcheck()

    catalog = load_json(CATALOG)
    if not catalog.get("beaches"):
        print("catalog.json missing beaches", file=sys.stderr)
        return 1

    entries = catalog["beaches"]
    if args.limit and args.limit > 0:
        entries = entries[: args.limit]

    prev_by_id = load_previous_state()
    api_key = os.environ.get("AEMET_API_KEY", "").strip()
    today = today_utc().isoformat()
    today_d = today_utc()

    # Copernicus subset once
    cmems_ok = False
    cmems_grid = None
    cmems_lats = cmems_lons = None
    if not args.skip_copernicus:
        print("fetching Copernicus IBI subset…", flush=True)
        got = fetch_copernicus_grid(today_d)
        if got:
            cmems_grid, cmems_lats, cmems_lons = got
            temps: list[float] = []
            samples: list[dict | None] = []
            for entry in entries:
                src = extract_copernicus_for_beach(
                    cmems_grid,
                    cmems_lats,
                    cmems_lons,
                    float(entry["lat"]),
                    float(entry["lon"]),
                    today,
                )
                samples.append(src)
                if src and day_for_date(src["days"], today):
                    temps.append(float(day_for_date(src["days"], today)["t"]))  # type: ignore[index]
            if copernicus_sane(temps, len(entries)):
                cmems_ok = True
                print(f"Copernicus OK beaches={len(temps)}", flush=True)
            else:
                print("Copernicus discarded for this run", file=sys.stderr)
                cmems_grid = None
        else:
            samples = [None] * len(entries)
    else:
        samples = [None] * len(entries)

    beaches_out: list[dict] = []
    fresh_mg = 0
    cat_ids = []

    for idx, entry in enumerate(entries):
        bid = entry["id"]
        cat_ids.append(bid)
        prev = prev_by_id.get(bid)
        sources: list[dict] = []

        if not args.skip_mg:
            mg_days = fetch_mg(bid)
            time.sleep(0.3)
            if _mg_has_today(mg_days, today):
                fresh_mg += 1
                old_mg = previous_source(prev, "MeteoGalicia")
                today_point = day_for_date(mg_days or [], today)
                old_days = (old_mg or {}).get("days") or []
                prev_d0 = old_days[0] if old_days else None
                hist = merge_history(
                    (old_mg or {}).get("history"),
                    today_point,  # type: ignore[arg-type]
                    prev_day0=prev_d0 if isinstance(prev_d0, dict) else None,
                )
                sources.append(
                    {"name": "MeteoGalicia", "days": mg_days, "history": hist}
                )
            else:
                old = previous_source(prev, "MeteoGalicia")
                if old and source_fresh(old):
                    sources.append(dict(old))
        else:
            old = previous_source(prev, "MeteoGalicia")
            if old and source_fresh(old):
                sources.append(dict(old))
                if day_for_date(old.get("days") or [], today):
                    fresh_mg += 1

        aemet_id = entry.get("aemetId")
        if aemet_id and api_key:
            ae_days = fetch_aemet(str(aemet_id), api_key)
            time.sleep(2.0)
            if ae_days and day_for_date(ae_days, today):
                sources.append({"name": "AEMET", "days": ae_days})
            else:
                old = previous_source(prev, "AEMET")
                if old and source_fresh(old):
                    sources.append(dict(old))
        elif aemet_id:
            old = previous_source(prev, "AEMET")
            if old and source_fresh(old):
                sources.append(dict(old))

        if cmems_ok and cmems_grid is not None:
            csrc = samples[idx]
            if csrc is None:
                csrc = extract_copernicus_for_beach(
                    cmems_grid,
                    cmems_lats,
                    cmems_lons,
                    float(entry["lat"]),
                    float(entry["lon"]),
                    today,
                )
            if csrc:
                sources.append(csrc)
        else:
            old = previous_source(prev, "Copernicus")
            if old and source_fresh(old):
                sources.append(dict(old))

        sources = [s for s in sources if source_fresh(s)]

        beach = {
            "id": bid,
            "slug": entry["slug"],
            "name": entry["name"],
            "concello": entry["concello"],
            "concelloSlug": entry["concelloSlug"],
            "sources": sources,
        }
        attach_primary_fields(beach, today)
        beaches_out.append(beach)

    # id-set guards (skip when --limit)
    if not args.limit:
        out_ids = [b["id"] for b in beaches_out]
        if sorted(out_ids) != sorted(cat_ids):
            print("ABORT: id set != catalog", file=sys.stderr)
            return 3
        full_ids = [b["id"] for b in catalog["beaches"]]
        if sorted(out_ids) != sorted(full_ids):
            print("ABORT: id set != full catalog", file=sys.stderr)
            return 3

        covered = sum(1 for b in beaches_out if b.get("t") is not None)
        n = len(catalog["beaches"])
        if covered == 0:
            print(f"ABORT: coverage 0/{n}", file=sys.stderr)
            return 2
        if covered < n / 2:
            print(
                f"WARN: coverage {covered}/{n} < 50%",
                file=sys.stderr,
            )

    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    top = build_top(beaches_out)
    covered = sum(1 for b in beaches_out if b.get("t") is not None)

    if args.limit:
        print(
            f"dry-run --limit={args.limit} beaches={len(beaches_out)} "
            f"covered={covered} fresh_mg={fresh_mg} top={len(top)} "
            f"copernicus={'yes' if cmems_ok else 'no'} fetchedAt={fetched_at}"
        )
        return 0

    write_data_split(fetched_at, beaches_out)
    print(
        f"wrote data/ fresh_mg={fresh_mg} beaches={len(beaches_out)} "
        f"covered={covered} top={len(top)} "
        f"copernicus={'yes' if cmems_ok else 'no'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
