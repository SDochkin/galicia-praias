#!/usr/bin/env python3
"""Daily bake: MG + AEMET + Copernicus + MeteoSIX → data/index.json + data/<concello>.json."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog.json"
DATA_DIR = ROOT / "data"
BEACHES_LEGACY = ROOT / "beaches.json"

MG_URL = "https://servizos.meteogalicia.gal/mgrss/predicion/jsonPredPraia.action?idPraia={}"
AEMET_URL = "https://opendata.aemet.es/opendata/api/prediccion/especifica/playa/{}"
METEOSIX_URL = (
    "https://servizos.meteogalicia.gal/apiv5/getNumericForecastInfo"
)

CMEMS_DATASET = "cmems_mod_ibi_phy_anfc_0.027deg-2D_PT1H-m"
CMEMS_VAR = "thetao"
GAL_LAT = (41.7, 43.9)
GAL_LON = (-9.4, -6.7)
CMEMS_SEARCH_RADIUS = 2  # cells
HISTORY_DAYS = 7
# Source names are an invariant: "MeteoGalicia", "AEMET", "Copernicus", "MeteoSIX"
# bind PRIMARY_ORDER, previous_source, data/ history, and index.html costaOf/marOf.
# Renaming a source wipes accumulated history.
PRIMARY_ORDER = ("MeteoGalicia", "AEMET")
TOP_CAP = 8
TOP_PER_CONCELLO = 2

MADRID_TZ = ZoneInfo("Europe/Madrid")
METEOSIX_VARS = (
    "sea_water_temperature",
    "temperature",
    "wind",
    "significative_wave_height",
    "relative_peak_period",
    "precipitation_amount",
)
# 1:1 with METEOSIX_VARS. Wind units ms_deg (score is m/s; API default km/h).
METEOSIX_UNITS = ",,ms_deg,,,"
# Wave model: v5 A1 says SWAN → USWAN. Try live, first that returns wave values wins.
METEOSIX_MODEL_CANDIDATES = (
    "ROMS,WRF,WRF,USWAN,USWAN,WRF",
    "ROMS,WRF,WRF,SWAN,SWAN,WRF",
    "ROMS,WRF,WRF,WW3,WW3,WRF",
)

# Display score. 17/20 must match bandFor in index.html.
SCORE_WEIGHTS = {
    "water": 40,
    "air": 20,
    "wind": 15,
    "waves": 15,
    "rain": 10,
}
SCORE_WATER = ((14.0, 0.0), (17.0, 50.0), (20.0, 90.0), (22.0, 100.0))
SCORE_AIR = (
    (14.0, 0.0),
    (20.0, 60.0),
    (24.0, 100.0),
    (28.0, 100.0),
    (34.0, 50.0),
    (38.0, 0.0),
)
SCORE_WIND = ((2.0, 100.0), (5.0, 80.0), (8.0, 40.0), (12.0, 0.0))
SCORE_WAVES = ((0.3, 100.0), (0.5, 80.0), (1.2, 30.0), (2.0, 0.0))
SCORE_RAIN = ((0.0, 100.0), (0.2, 70.0), (1.0, 30.0), (3.0, 0.0))
H_EFF_PERIOD_S = 8.0
H_EFF_ADD_M = 0.3
SCORE_WINDOW_DROP = 5
SCORE_HOUR_START = 10
SCORE_HOUR_END = 20
WAVE_CALM_M = 0.5
WAVE_STRONG_M = 1.2
SCORE_FIELD_KEYS = (
    "score",
    "scoreParts",
    "bestHour",
    "scoreWindow",
    "scoreDay",
    "wave",
)


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


def mg_days_from_payload(data: dict) -> list[dict] | None:
    days = data["predPraia"]["listaPredDiaPraia"]
    out = []
    for d in days[:3]:
        t = d.get("tAuga")
        if t is None or t == -9999:
            continue
        raw = d["dataPredicion"]
        day = raw[:10] if isinstance(raw, str) else str(raw)[:10]
        rec: dict[str, Any] = {"date": day, "t": _round_t(t)}
        uv = d.get("uvMax")
        if isinstance(uv, (int, float)) and uv != -9999:
            rec["uv"] = uv
        out.append(rec)
    if not out:
        return None
    return out


def fetch_mg(beach_id: int) -> list[dict] | None:
    url = MG_URL.format(beach_id)
    for attempt in range(3):
        try:
            body, _ = http_get(url)
            data = json.loads(body.decode("utf-8"))
            return mg_days_from_payload(data)
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
        if s.get("name") != "Copernicus":
            continue
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


def piecewise(x: float, points: tuple[tuple[float, float], ...]) -> float:
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    for i in range(len(points) - 1):
        x0, y0 = points[i]
        x1, y1 = points[i + 1]
        if x0 <= x <= x1:
            if x1 == x0:
                return y0
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def h_eff(h: float, period: float | None) -> float:
    if period is not None and period < H_EFF_PERIOD_S:
        return h + H_EFF_ADD_M
    return h


def wave_level(h: float, period: float | None) -> str:
    he = h_eff(h, period)
    if he < WAVE_CALM_M:
        return "calm"
    if he <= WAVE_STRONG_M:
        return "moderate"
    return "strong"


def _renorm_weights(parts: list[str]) -> dict[str, float]:
    raw = {k: SCORE_WEIGHTS[k] for k in parts}
    total = sum(raw.values())
    return {k: v * 100.0 / total for k, v in raw.items()}


def score_from_parts(parts: dict[str, float]) -> int:
    keys = [k for k in SCORE_WEIGHTS if k in parts]
    w = _renorm_weights(keys)
    return round(sum(parts[k] * w[k] / 100.0 for k in keys))


def _parse_timeinstant(raw: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _num(v: Any) -> float | None:
    if isinstance(v, (int, float)) and not math.isnan(float(v)):
        return float(v)
    return None


def _var_values(variables: list[dict], name: str) -> list[dict]:
    for v in variables:
        if v.get("name") == name:
            vals = v.get("values")
            return vals if isinstance(vals, list) else []
    return []


def parse_meteosix_feature(feat: dict) -> tuple[list[dict], list[dict]]:
    """Return (hours, days_t). hours stay in memory; days go to JSON.

    JSON keys: docs/meteosix-api.md §Ответ JSON.
    """
    if feat.get("exception"):
        return [], []
    props = feat.get("properties")
    if not isinstance(props, dict):
        return [], []
    days_in = props.get("days")
    if not isinstance(days_in, list):
        return [], []
    by_dt: dict[datetime, dict[str, Any]] = {}
    for day in days_in:
        if not isinstance(day, dict):
            continue
        variables = day.get("variables")
        if not isinstance(variables, list):
            continue
        buckets: dict[str, list[dict]] = {
            "water": _var_values(variables, "sea_water_temperature"),
            "air": _var_values(variables, "temperature"),
            "wind": _var_values(variables, "wind"),
            "h": _var_values(variables, "significative_wave_height"),
            "period": _var_values(variables, "relative_peak_period"),
            "rain": _var_values(variables, "precipitation_amount"),
        }
        for key, rows in buckets.items():
            field = "moduleValue" if key == "wind" else "value"
            for row in rows:
                if not isinstance(row, dict):
                    continue
                dt = _parse_timeinstant(str(row.get("timeInstant") or ""))
                if dt is None:
                    continue
                rec = by_dt.setdefault(dt, {"dt": dt})
                rec[key] = _num(row.get(field))
    hours = [by_dt[k] for k in sorted(by_dt)]
    by_date: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    for rec in hours:
        t = rec.get("water")
        if not isinstance(t, float):
            continue
        day = rec["dt"].date().isoformat()
        by_date[day].append((rec["dt"], t))
    days_out: list[dict] = []
    for day, pts in sorted(by_date.items()):
        best = min(pts, key=lambda p: abs((p[0].hour * 60 + p[0].minute) - 12 * 60))
        days_out.append({"date": day, "t": _round_t(best[1])})
    return hours, days_out


def _meteosix_params(
    coords: list[tuple[float, float]], api_key: str, models: str
) -> dict[str, str]:
    return {
        "API_KEY": api_key,
        "coords": ";".join(f"{lon},{lat}" for lon, lat in coords),
        "variables": ",".join(METEOSIX_VARS),
        "models": models,
        "units": METEOSIX_UNITS,
        "format": "application/json",
    }


def fetch_meteosix_payload(
    coords: list[tuple[float, float]], api_key: str, models: str
) -> dict | None:
    if not coords:
        return None
    url = METEOSIX_URL + "?" + urllib.parse.urlencode(_meteosix_params(coords, api_key, models))
    try:
        body, _ = http_get(url, timeout=60)
        data = json.loads(body.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    exc = data.get("exception")
    if isinstance(exc, dict):
        return None
    return data


def _probe_raw_vars(feat: dict) -> None:
    """Print name/model/grid and non-null value counts. No values, no key."""
    props = feat.get("properties") if isinstance(feat, dict) else None
    days = props.get("days") if isinstance(props, dict) else None
    if not isinstance(days, list) or not days or not isinstance(days[0], dict):
        print("MeteoSIX probe raw: no days", flush=True)
        return
    variables = days[0].get("variables")
    if not isinstance(variables, list):
        print("MeteoSIX probe raw: variables not list", flush=True)
        return
    bits: list[str] = []
    for var in variables:
        if not isinstance(var, dict):
            continue
        vals = var.get("values")
        n_ok = 0
        field = "moduleValue" if var.get("name") == "wind" else "value"
        if isinstance(vals, list):
            for row in vals:
                if isinstance(row, dict) and _num(row.get(field)) is not None:
                    n_ok += 1
        bits.append(
            f"{var.get('name')}/{var.get('model')}/{var.get('grid')} non_null={n_ok}"
        )
    print("MeteoSIX probe raw: " + "; ".join(bits), flush=True)


def probe_meteosix_models(api_key: str, lon: float, lat: float) -> str | None:
    """Live USWAN vs SWAN vs WW3. Prefer a string that returns wave values."""
    fallback: str | None = None
    for models in METEOSIX_MODEL_CANDIDATES:
        data = fetch_meteosix_payload([(lon, lat)], api_key, models)
        feats = (data or {}).get("features") or []
        tag = models.split(",")[3]
        if not feats:
            print(f"MeteoSIX probe {tag}: no features", flush=True)
            continue
        feat = feats[0]
        if feat.get("exception"):
            print(f"MeteoSIX probe {tag}: {feat.get('exception')}", flush=True)
            continue
        _probe_raw_vars(feat)
        hours, days = parse_meteosix_feature(feat)
        n_water = sum(1 for h in hours if isinstance(h.get("water"), float))
        n_wave = sum(1 for h in hours if isinstance(h.get("h"), float))
        print(
            f"MeteoSIX probe {tag}: hours={len(hours)} water={n_water} "
            f"waves={n_wave} days_t={len(days)}",
            flush=True,
        )
        if n_wave:
            return models
        if hours and fallback is None:
            fallback = models
    return fallback


def wave_at_noon(hours: list[dict], day: str) -> str | None:
    with_h = [h for h in hours if isinstance(h.get("h"), float)]
    if not with_h:
        return None
    by_date: dict[str, list[dict]] = defaultdict(list)
    for rec in with_h:
        by_date[rec["dt"].date().isoformat()].append(rec)
    pts = by_date.get(day)
    if not pts:
        return None
    rec = min(pts, key=lambda p: abs((p["dt"].hour * 60 + p["dt"].minute) - 12 * 60))
    period = rec["period"] if isinstance(rec.get("period"), float) else None
    return wave_level(rec["h"], period)


def fetch_meteosix_batch(
    coords: list[tuple[float, float]], api_key: str, models: str
) -> list[dict | None]:
    data = fetch_meteosix_payload(coords, api_key, models)
    feats = (data or {}).get("features") if data else None
    if not isinstance(feats, list):
        return [None] * len(coords)
    out: list[dict | None] = []
    for i, _c in enumerate(coords):
        feat = feats[i] if i < len(feats) else None
        if not isinstance(feat, dict):
            out.append(None)
            continue
        hours, days = parse_meteosix_feature(feat)
        if not days and not hours:
            out.append(None)
            continue
        out.append({"hours": hours, "days": days})
    return out


def fetch_all_meteosix(
    entries: list[dict], api_key: str, models: str
) -> list[dict | None]:
    out: list[dict | None] = []
    batch: list[tuple[float, float]] = []
    batch_i: list[int] = []

    def flush() -> None:
        nonlocal batch, batch_i
        if not batch:
            return
        got = fetch_meteosix_batch(batch, api_key, models)
        for j, rec in enumerate(got):
            while len(out) < batch_i[j] + 1:
                out.append(None)
            out[batch_i[j]] = rec
        batch = []
        batch_i = []
        time.sleep(0.2)

    for i, entry in enumerate(entries):
        batch.append((float(entry["lon"]), float(entry["lat"])))
        batch_i.append(i)
        if len(batch) >= 20:
            flush()
    flush()
    while len(out) < len(entries):
        out.append(None)
    return out


def meteosix_hourly(mx: dict | None) -> list[dict] | None:
    if not mx:
        return None
    hours = mx.get("hours")
    return hours if isinstance(hours, list) else None


def append_meteosix_source(
    sources: list[dict],
    *,
    mx: dict | None,
    fetched_hourly: bool,
    today: str,
    prev: dict | None,
) -> None:
    """Write MeteoSIX into sources[] only when days have a t for today."""
    if fetched_hourly and mx:
        days_mx = mx.get("days") or []
        today_point = day_for_date(days_mx, today) if days_mx else None
        if today_point:
            old_mx = previous_source(prev, "MeteoSIX")
            old_days = (old_mx or {}).get("days") or []
            prev_d0 = old_days[0] if old_days else None
            hist = merge_history(
                (old_mx or {}).get("history"),
                today_point,
                prev_day0=prev_d0 if isinstance(prev_d0, dict) else None,
            )
            sources.append(
                {"name": "MeteoSIX", "days": days_mx, "history": hist}
            )
            return
    old = previous_source(prev, "MeteoSIX")
    if old and source_fresh(old):
        sources.append(dict(old))


def hour_parts(rec: dict) -> dict[str, float]:
    parts: dict[str, float] = {}
    if isinstance(rec.get("air"), float):
        parts["air"] = piecewise(rec["air"], SCORE_AIR)
    if isinstance(rec.get("wind"), float):
        parts["wind"] = piecewise(rec["wind"], SCORE_WIND)
    if isinstance(rec.get("h"), float):
        period = rec["period"] if isinstance(rec.get("period"), float) else None
        parts["waves"] = piecewise(h_eff(rec["h"], period), SCORE_WAVES)
    if isinstance(rec.get("rain"), float):
        parts["rain"] = piecewise(rec["rain"], SCORE_RAIN)
    return parts


def filter_score_hours(
    hours: list[dict], now: datetime
) -> list[dict]:
    """Keep hours on now's Madrid calendar day in [SCORE_HOUR_START, SCORE_HOUR_END)."""
    now_m = now.astimezone(MADRID_TZ)
    day = now_m.date()
    out = []
    for rec in hours:
        dt = rec["dt"].astimezone(MADRID_TZ)
        if dt.date() != day:
            continue
        if not (SCORE_HOUR_START <= dt.hour < SCORE_HOUR_END):
            continue
        out.append(rec)
    return out


def drop_holey_hours(rows: list[tuple[dict, dict[str, float]]]) -> list[tuple[dict, dict[str, float]]]:
    if not rows:
        return []
    union: set[str] = set()
    for _rec, parts in rows:
        union |= set(parts)
    kept = [(rec, parts) for rec, parts in rows if set(parts) >= union]
    return kept


def compute_score(
    hours: list[dict],
    water_t: float | None,
    now: datetime,
) -> dict[str, Any]:
    empty = {
        "score": None,
        "scoreParts": None,
        "bestHour": None,
        "scoreWindow": None,
    }
    if not isinstance(water_t, (int, float)):
        return empty
    water_s = piecewise(float(water_t), SCORE_WATER)
    remaining = filter_score_hours(hours, now)
    if not remaining:
        return empty
    rows = [(rec, hour_parts(rec)) for rec in remaining]
    rows = drop_holey_hours(rows)
    if not rows:
        return empty
    common = set(rows[0][1])
    for _rec, parts in rows[1:]:
        common &= set(parts)
    keys = ["water"] + [k for k in SCORE_WEIGHTS if k != "water" and k in common]
    scored: list[tuple[dict, dict[str, float], int]] = []
    for rec, parts in rows:
        full = {"water": water_s, **{k: parts[k] for k in keys if k != "water"}}
        scored.append((rec, full, score_from_parts(full)))
    best_i = 0
    best_score = scored[0][2]
    for i, (rec, _p, sc) in enumerate(scored):
        if sc > best_score or (
            sc == best_score and rec["dt"] < scored[best_i][0]["dt"]
        ):
            best_score = sc
            best_i = i
    floor = best_score - SCORE_WINDOW_DROP
    left = best_i
    while left > 0 and scored[left - 1][2] >= floor:
        gap = scored[left][0]["dt"] - scored[left - 1][0]["dt"]
        if gap > timedelta(hours=1):
            break
        left -= 1
    right = best_i
    while right + 1 < len(scored) and scored[right + 1][2] >= floor:
        gap = scored[right + 1][0]["dt"] - scored[right][0]["dt"]
        if gap > timedelta(hours=1):
            break
        right += 1
    best_parts = scored[best_i][1]
    return {
        "score": scored[best_i][2],
        "scoreParts": {k: round(best_parts[k]) for k in keys},
        "bestHour": scored[best_i][0]["dt"].astimezone(timezone.utc).hour,
        "scoreWindow": {
            "from": scored[left][0]["dt"].astimezone(timezone.utc).hour,
            "to": scored[right][0]["dt"].astimezone(timezone.utc).hour,
        },
    }


def resolve_copernicus_source(
    csrc: dict | None, prev: dict | None
) -> dict | None:
    if csrc:
        return csrc
    old = previous_source(prev, "Copernicus")
    if old and source_fresh(old):
        return old
    return None


def apply_score_fields(
    beach: dict,
    *,
    hourly: list[dict] | None,
    fetched_hourly: bool,
    now: datetime,
    prev: dict | None,
) -> None:
    today_madrid = now.astimezone(MADRID_TZ).date().isoformat()
    if fetched_hourly:
        result = compute_score(hourly or [], beach.get("t"), now)
        if result["score"] is not None:
            beach["score"] = result["score"]
            beach["scoreParts"] = result["scoreParts"]
            beach["bestHour"] = result["bestHour"]
            beach["scoreWindow"] = result["scoreWindow"]
            beach["scoreDay"] = today_madrid
            w = wave_at_noon(
                hourly or [], datetime.now(timezone.utc).date().isoformat()
            )
            if w is not None:
                beach["wave"] = w
            return
    if not prev:
        return
    has_any = any(k in prev for k in SCORE_FIELD_KEYS)
    if not has_any:
        return
    for k in SCORE_FIELD_KEYS:
        if k in prev:
            beach[k] = prev[k]
    if prev.get("scoreDay") != today_madrid:
        return
    if not isinstance(beach.get("t"), (int, float)):
        return
    parts = beach.get("scoreParts")
    if not isinstance(parts, dict) or "water" not in parts:
        return
    new_parts = dict(parts)
    new_parts["water"] = round(piecewise(float(beach["t"]), SCORE_WATER))
    beach["scoreParts"] = new_parts
    beach["score"] = score_from_parts(new_parts)


def concello_beach_record(b: dict) -> dict:
    rec: dict[str, Any] = {
        "id": b["id"],
        "slug": b["slug"],
        "name": b["name"],
        "t": b.get("t"),
        "source": b.get("source"),
        "trend": b.get("trend"),
        "sources": b.get("sources") or [],
    }
    for k in SCORE_FIELD_KEYS:
        if k in b:
            rec[k] = b[k]
    return rec


def geo_entry(b: dict) -> dict:
    return {
        "slug": b["slug"],
        "name": b["name"],
        "concelloSlug": b["concelloSlug"],
        "lat": b["lat"],
        "lon": b["lon"],
        "t": b.get("t"),
        "score": b.get("score"),
    }


def feature_var_presence(feat: dict) -> dict[str, bool]:
    hours, _ = parse_meteosix_feature(feat)
    return {
        "sea_water_temperature": any(isinstance(h.get("water"), float) for h in hours),
        "temperature": any(isinstance(h.get("air"), float) for h in hours),
        "wind": any(isinstance(h.get("wind"), float) for h in hours),
        "significative_wave_height": any(isinstance(h.get("h"), float) for h in hours),
        "relative_peak_period": any(isinstance(h.get("period"), float) for h in hours),
        "precipitation_amount": any(isinstance(h.get("rain"), float) for h in hours),
    }


def run_coverage(catalog: dict) -> int:
    api_key = os.environ.get("METEOSIX_API_KEY", "").strip()
    if not api_key:
        print("METEOSIX_API_KEY missing", file=sys.stderr)
        return 1
    beaches = catalog.get("beaches") or []
    if not beaches:
        print("catalog.json missing beaches", file=sys.stderr)
        return 1
    first = beaches[0]
    print(
        f"probe point {first.get('slug')} lon={first['lon']} lat={first['lat']}",
        flush=True,
    )
    models = probe_meteosix_models(api_key, float(first["lon"]), float(first["lat"]))
    if not models:
        print("MeteoSIX probe failed for all models=", file=sys.stderr)
        return 1
    print(f"using models={models}", flush=True)
    n = len(beaches)
    counts = {v: 0 for v in METEOSIX_VARS}
    ok = 0
    errors = 0
    for i in range(0, n, 20):
        chunk = beaches[i : i + 20]
        coords = [(float(b["lon"]), float(b["lat"])) for b in chunk]
        data = fetch_meteosix_payload(coords, api_key, models)
        feats = (data or {}).get("features") if data else None
        if not isinstance(feats, list):
            errors += len(chunk)
            print(f"batch {i}-{i + len(chunk) - 1}: no features", flush=True)
            continue
        for j, _b in enumerate(chunk):
            feat = feats[j] if j < len(feats) else None
            if not isinstance(feat, dict) or feat.get("exception"):
                errors += 1
                continue
            ok += 1
            pres = feature_var_presence(feat)
            for v, hit in pres.items():
                if hit:
                    counts[v] += 1
        print(f"coverage {min(i + 20, n)}/{n}", flush=True)
        time.sleep(0.2)
    print(f"beaches={n} features_ok={ok} errors={errors} models={models}")
    for v in METEOSIX_VARS:
        print(f"  {v}: {counts[v]}/{n} ({100.0 * counts[v] / n:.1f}%)")
    # dump schema keys from first successful feature
    data0 = fetch_meteosix_payload(
        [(float(first["lon"]), float(first["lat"]))], api_key, models
    )
    feats0 = (data0 or {}).get("features") or []
    if feats0 and isinstance(feats0[0], dict) and not feats0[0].get("exception"):
        f0 = feats0[0]
        print("schema feature keys:", sorted(f0.keys()))
        props = f0.get("properties") or {}
        print("schema properties keys:", sorted(props.keys()) if isinstance(props, dict) else props)
        days = props.get("days") if isinstance(props, dict) else None
        if isinstance(days, list) and days:
            print("schema day keys:", sorted(days[0].keys()) if isinstance(days[0], dict) else days[0])
            variables = days[0].get("variables") if isinstance(days[0], dict) else None
            if isinstance(variables, list) and variables:
                print("schema variable keys:", sorted(variables[0].keys()))
                vals = variables[0].get("values")
                if isinstance(vals, list) and vals:
                    print("schema value keys:", sorted(vals[0].keys()))
    return 0


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
                "score": b.get("score"),
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
        by_concello[slug].append(concello_beach_record(b))

    concellos = [
        {"slug": s, "name": concello_names[s]}
        for s in sorted(concello_names, key=lambda x: concello_names[x].casefold())
    ]
    index = {
        "fetchedAt": fetched_at,
        "concellos": concellos,
        "beachConcello": beach_map,
        "top": build_top(beaches),
        "geo": [geo_entry(b) for b in beaches],
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
    uv_top = build_top(
        [
            {
                "slug": "uv-1",
                "name": "UV1",
                "concello": "Hot",
                "concelloSlug": "hot",
                "t": 20.0,
                "source": "MeteoGalicia",
                "trend": None,
                "sources": [
                    {
                        "name": "MeteoGalicia",
                        "days": [{"date": today, "t": 20.0, "uv": 7}],
                    }
                ],
            }
        ]
    )
    assert uv_top[0]["sources"][0]["days"][0]["uv"] == 7

    # live-shaped MG payload (jsonPredPraia idPraia=2444, 2026-08-20)
    mg_sample = {
        "predPraia": {
            "idPraia": 2444,
            "listaPredDiaPraia": [
                {
                    "dataPredicion": "2026-08-20T00:00:00",
                    "tAuga": 15,
                    "uvMax": 7,
                },
                {
                    "dataPredicion": "2026-08-21T00:00:00",
                    "tAuga": 15,
                    "uvMax": 7,
                },
                {
                    "dataPredicion": "2026-08-22T00:00:00",
                    "tAuga": 14,
                    "uvMax": 7,
                },
                {
                    "dataPredicion": "2026-08-23T00:00:00",
                    "tAuga": 14,
                    "uvMax": 6,
                },
            ],
        }
    }
    mg_days = mg_days_from_payload(mg_sample)
    assert mg_days == [
        {"date": "2026-08-20", "t": 15.0, "uv": 7},
        {"date": "2026-08-21", "t": 15.0, "uv": 7},
        {"date": "2026-08-22", "t": 14.0, "uv": 7},
    ], mg_days
    assert mg_days_from_payload(
        {
            "predPraia": {
                "listaPredDiaPraia": [
                    {"dataPredicion": "2026-08-20T00:00:00", "tAuga": -9999, "uvMax": 8}
                ]
            }
        }
    ) is None

    # pick_primary: MeteoSIX is never fallback
    t, src = pick_primary(
        [
            {"name": "MeteoGalicia", "days": [{"date": today, "t": 14.0}]},
            {"name": "AEMET", "days": [{"date": today, "t": 15.0}]},
            {"name": "Copernicus", "days": [{"date": today, "t": 16.0}]},
            {"name": "MeteoSIX", "days": [{"date": today, "t": 18.0}]},
        ],
        today,
    )
    assert (t, src) == (14.0, "MeteoGalicia"), (t, src)
    t, src = pick_primary(
        [
            {"name": "Copernicus", "days": [{"date": today, "t": 16.0}]},
            {"name": "MeteoSIX", "days": [{"date": today, "t": 18.0}]},
        ],
        today,
    )
    assert (t, src) == (16.0, "Copernicus"), (t, src)
    t, src = pick_primary(
        [{"name": "MeteoSIX", "days": [{"date": today, "t": 18.0}]}], today
    )
    assert (t, src) == (None, None), (t, src)

    assert wave_level(0.3, 10) == "calm"
    assert wave_level(0.4, 6) == "moderate"  # h_eff=0.7
    assert wave_level(0.5, 10) == "moderate"
    assert wave_level(1.2, 10) == "moderate"
    assert wave_level(1.3, 10) == "strong"
    noon_h = [
        {
            "dt": datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
            "h": 0.3,
            "period": 10.0,
        }
    ]
    assert wave_at_noon(noon_h, "2026-08-20") == "calm"
    assert wave_at_noon(noon_h, "2026-08-19") is None

    # score: now= 12:00 Madrid 2026-08-20 (CEST = UTC+2)
    now = datetime(2026, 8, 20, 12, 0, tzinfo=MADRID_TZ)
    water = 20.0  # → 90

    def hr(h: int, **kw: Any) -> dict:
        return {
            "dt": datetime(2026, 8, 20, h, 0, tzinfo=MADRID_TZ).astimezone(timezone.utc),
            "air": 24.0,
            "wind": 2.0,
            "h": 0.3,
            "period": 10.0,
            "rain": 0.0,
            **kw,
        }

    # 11:00 Madrid is in 10–20 and ties 13:00; earlier slot wins.
    hours = [
        hr(11, wind=2.0),
        hr(12, wind=5.0),
        hr(13, wind=2.0),
        hr(14, wind=5.0),
    ]
    got = compute_score(hours, water, now)
    # 90*0.4 + 100*0.2 + 100*0.15 + 100*0.15 + 100*0.10 = 96
    assert got["score"] == 96, got
    assert got["bestHour"] == 9, got  # 11:00 Madrid = 09:00 UTC
    assert got["scoreWindow"] == {"from": 9, "to": 12}, got
    assert got["scoreParts"]["water"] == 90

    now_eve = datetime(2026, 8, 20, 18, 0, tzinfo=MADRID_TZ)
    got_morn = compute_score([hr(11, wind=2.0), hr(18, wind=5.0)], water, now_eve)
    assert got_morn["bestHour"] == 9, got_morn

    # holey 13:00 (no wind) cannot win; window does not jump the hole
    hours_hole = [
        hr(12, wind=5.0),
        {"dt": datetime(2026, 8, 20, 13, 0, tzinfo=MADRID_TZ).astimezone(timezone.utc),
         "air": 24.0, "h": 0.3, "period": 10.0, "rain": 0.0},
        hr(14, wind=2.0),
    ]
    got_h = compute_score(hours_hole, water, now)
    assert got_h["bestHour"] == 12, got_h  # 14:00 Madrid = 12:00 UTC
    assert got_h["scoreWindow"] == {"from": 12, "to": 12}, got_h

    # carry: yesterday scoreDay + skip keeps fields; today + skip + new t updates water
    prev_y = {
        "score": 88,
        "scoreParts": {"water": 50, "air": 60, "wind": 80, "waves": 40, "rain": 100},
        "bestHour": 11,
        "scoreWindow": {"from": 10, "to": 12},
        "scoreDay": "2026-08-19",
        "wave": "calm",
    }
    b1 = {"t": 20.0}
    apply_score_fields(
        b1, hourly=None, fetched_hourly=False, now=now, prev=prev_y
    )
    assert b1["score"] == 88 and b1["scoreDay"] == "2026-08-19"
    assert b1["scoreParts"]["water"] == 50
    prev_t = dict(prev_y)
    prev_t["scoreDay"] = "2026-08-20"
    b2 = {"t": 22.0}
    apply_score_fields(
        b2, hourly=None, fetched_hourly=False, now=now, prev=prev_t
    )
    assert b2["scoreParts"]["water"] == 100, b2["scoreParts"]
    assert b2["bestHour"] == 11
    assert b2["score"] == score_from_parts(b2["scoreParts"])

    b_empty = {"t": 22.0}
    apply_score_fields(
        b_empty, hourly=[], fetched_hourly=True, now=now, prev=prev_t
    )
    assert b_empty["scoreParts"]["water"] == 100, b_empty["scoreParts"]
    assert b_empty["bestHour"] == 11
    assert b_empty["score"] == score_from_parts(b_empty["scoreParts"])
    assert b_empty["scoreDay"] == "2026-08-20"

    ae_today = {"date": "2026-08-20", "t": 19.5}
    old_ae = {
        "name": "AEMET",
        "days": [{"date": "2026-08-19", "t": 18.0}, {"date": "2026-08-20", "t": 19.0}],
        "history": [],
    }
    ae_hist = merge_history(
        old_ae.get("history"),
        ae_today,
        prev_day0=old_ae["days"][0],
    )
    assert any(h["date"] == "2026-08-19" for h in ae_hist), ae_hist

    fresh_day = today_utc().isoformat()
    stale_day = (today_utc() - timedelta(days=10)).isoformat()
    fresh_cop = {
        "name": "Copernicus",
        "days": [{"date": fresh_day, "t": 16.0}],
    }
    stale_cop = {
        "name": "Copernicus",
        "days": [{"date": stale_day, "t": 16.0}],
    }
    live = {"name": "Copernicus", "days": [{"date": fresh_day, "t": 17.0}]}
    assert resolve_copernicus_source(live, {"sources": [fresh_cop]}) is live
    assert (
        resolve_copernicus_source(None, {"sources": [fresh_cop]}) is fresh_cop
    )
    assert resolve_copernicus_source(None, {"sources": [stale_cop]}) is None

    rec = concello_beach_record(
        {
            "id": 1,
            "slug": "x",
            "name": "X",
            "t": 20.0,
            "source": "MeteoGalicia",
            "trend": None,
            "sources": [],
            "score": 96,
            "wave": "calm",
            "scoreDay": "2026-08-20",
        }
    )
    assert rec["score"] == 96 and rec["wave"] == "calm"
    g = geo_entry(
        {
            "slug": "x",
            "name": "X",
            "concelloSlug": "c",
            "lat": 43.0,
            "lon": -8.0,
            "t": 20.0,
            "score": 96,
        }
    )
    assert g == {
        "slug": "x",
        "name": "X",
        "concelloSlug": "c",
        "lat": 43.0,
        "lon": -8.0,
        "t": 20.0,
        "score": 96,
    }

    # hours without days: score still written; no MeteoSIX source
    mx_no_t = {"hours": hours, "days": []}
    srcs_no_t: list[dict] = []
    append_meteosix_source(
        srcs_no_t,
        mx=mx_no_t,
        fetched_hourly=True,
        today="2026-08-20",
        prev=None,
    )
    b3 = {"t": 20.0, "sources": srcs_no_t}
    apply_score_fields(
        b3,
        hourly=meteosix_hourly(mx_no_t),
        fetched_hourly=True,
        now=now,
        prev=None,
    )
    assert b3["score"] == 96, b3
    assert srcs_no_t == []
    srcs_with_t: list[dict] = []
    append_meteosix_source(
        srcs_with_t,
        mx={"hours": hours, "days": [{"date": "2026-08-20", "t": 18.0}]},
        fetched_hourly=True,
        today="2026-08-20",
        prev=None,
    )
    assert srcs_with_t[0]["name"] == "MeteoSIX"
    assert srcs_with_t[0]["days"][0]["t"] == 18.0

    print("selfcheck OK")
    return 0


def _mg_has_today(days: list[dict] | None, today: str) -> bool:
    return bool(days and day_for_date(days, today))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="First N catalog beaches (dry-run)")
    parser.add_argument("--selfcheck", action="store_true")
    parser.add_argument("--coverage", action="store_true", help="MeteoSIX coverage; do not write data/")
    parser.add_argument("--skip-mg", action="store_true", help="Skip MG fetch (dev)")
    parser.add_argument("--skip-copernicus", action="store_true")
    parser.add_argument("--skip-meteosix", action="store_true")
    parser.add_argument("--skip-aemet", action="store_true")
    args = parser.parse_args(argv)

    if args.selfcheck:
        return run_selfcheck()

    catalog = load_json(CATALOG)
    if not catalog.get("beaches"):
        print("catalog.json missing beaches", file=sys.stderr)
        return 1

    if args.coverage:
        return run_coverage(catalog)

    entries = catalog["beaches"]
    if args.limit and args.limit > 0:
        entries = entries[: args.limit]

    prev_by_id = load_previous_state()
    api_key = os.environ.get("AEMET_API_KEY", "").strip()
    meteosix_key = os.environ.get("METEOSIX_API_KEY", "").strip()
    today = today_utc().isoformat()
    today_d = today_utc()
    now_madrid = datetime.now(MADRID_TZ)

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

    meteosix_models: str | None = None
    meteosix_got: list[dict | None] = [None] * len(entries)
    fetched_hourly = False
    if not args.skip_meteosix and meteosix_key:
        first = entries[0]
        print("probing MeteoSIX models=…", flush=True)
        meteosix_models = probe_meteosix_models(
            meteosix_key, float(first["lon"]), float(first["lat"])
        )
        if meteosix_models:
            print(f"fetching MeteoSIX models={meteosix_models}…", flush=True)
            meteosix_got = fetch_all_meteosix(entries, meteosix_key, meteosix_models)
            fetched_hourly = True
            n_days = sum(1 for x in meteosix_got if x and x.get("days"))
            n_hours = sum(1 for x in meteosix_got if x and x.get("hours"))
            print(
                f"MeteoSIX days={n_days} hours={n_hours} / {len(entries)}",
                flush=True,
            )
        else:
            print("MeteoSIX probe failed; carrying stale", file=sys.stderr)
    elif not args.skip_meteosix and not meteosix_key:
        print("METEOSIX_API_KEY missing; carrying stale", file=sys.stderr)

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
        if aemet_id and api_key and not args.skip_aemet:
            ae_days = fetch_aemet(str(aemet_id), api_key)
            time.sleep(2.0)
            if ae_days and day_for_date(ae_days, today):
                old_ae = previous_source(prev, "AEMET")
                today_point = day_for_date(ae_days, today)
                old_days = (old_ae or {}).get("days") or []
                prev_d0 = old_days[0] if old_days else None
                hist = merge_history(
                    (old_ae or {}).get("history"),
                    today_point,  # type: ignore[arg-type]
                    prev_day0=prev_d0 if isinstance(prev_d0, dict) else None,
                )
                sources.append(
                    {"name": "AEMET", "days": ae_days, "history": hist}
                )
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
            csrc = resolve_copernicus_source(csrc, prev)
            if csrc:
                sources.append(csrc)
        else:
            old = previous_source(prev, "Copernicus")
            if old and source_fresh(old):
                sources.append(dict(old))

        mx = meteosix_got[idx] if idx < len(meteosix_got) else None
        hourly = meteosix_hourly(mx) if fetched_hourly else None
        append_meteosix_source(
            sources,
            mx=mx,
            fetched_hourly=fetched_hourly,
            today=today,
            prev=prev,
        )

        sources = [s for s in sources if source_fresh(s)]

        beach = {
            "id": bid,
            "slug": entry["slug"],
            "name": entry["name"],
            "concello": entry["concello"],
            "concelloSlug": entry["concelloSlug"],
            "lat": entry["lat"],
            "lon": entry["lon"],
            "sources": sources,
        }
        attach_primary_fields(beach, today)
        apply_score_fields(
            beach,
            hourly=hourly,
            fetched_hourly=fetched_hourly,
            now=now_madrid,
            prev=prev,
        )
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
            f"copernicus={'yes' if cmems_ok else 'no'} "
            f"meteosix={'yes' if fetched_hourly else 'no'} fetchedAt={fetched_at}"
        )
        return 0

    write_data_split(fetched_at, beaches_out)
    print(
        f"wrote data/ fresh_mg={fresh_mg} beaches={len(beaches_out)} "
        f"covered={covered} top={len(top)} "
        f"copernicus={'yes' if cmems_ok else 'no'} "
        f"meteosix={'yes' if fetched_hourly else 'no'}"
    )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
