#!/usr/bin/env python3
"""Compare catalog.json points to OSM Nominatim. Does not write catalog or data/.

Evidence 2026-08-23:
- Policy: https://operations.osmfoundation.org/policies/nominatim/
  max 1 request/s; valid User-Agent; one-time bulk is single-thread.
- Live GET (User-Agent galicia-praias/diag_geotag):
  https://nominatim.openstreetmap.org/search?q=Praia+do+Cabo+Redondela&format=json&limit=3
  HTTP 200. First hit: lat 42.3098872 lon -8.6238043 (degrees WGS84),
  class=natural type=beach name=O Vao do Cabo.
- Distance unit in this script: km (haversine, R=6371).

Point or name fixes go through NAME_OVERRIDES in scripts/build_catalog.py
and only when the owner names specific ids.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog.json"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "galicia-praias/diag_geotag (https://github.com/SDochkin/galicia-praias)"
PAUSE_S = 1.1


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (
        math.sin(dp / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))


def nominatim_search(q: str) -> dict | None:
    params = {
        "q": q,
        "format": "json",
        "limit": "1",
        "countrycodes": "es",
    }
    url = NOMINATIM + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=45) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if not isinstance(body, list) or not body:
        return None
    hit = body[0]
    if not isinstance(hit, dict):
        return None
    try:
        lat = float(hit["lat"])
        lon = float(hit["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "lat": lat,
        "lon": lon,
        "name": hit.get("display_name") or hit.get("name") or "",
    }


def lookup(name: str, concello: str) -> dict | None:
    queries = [
        f"{name} {concello}",
        f"{name}, {concello}, Galicia, España",
    ]
    for i, q in enumerate(queries):
        if i:
            time.sleep(PAUSE_S)
        try:
            hit = nominatim_search(q)
        except Exception as exc:  # noqa: BLE001
            print(f"WARN {q}: {exc}", file=sys.stderr)
            continue
        if hit:
            return hit
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args(argv)
    catalog = load_json(CATALOG)
    beaches = list(catalog.get("beaches") or [])
    if args.limit and args.limit > 0:
        beaches = beaches[: args.limit]
    if not beaches:
        print("catalog.json missing beaches", file=sys.stderr)
        return 1
    print(
        "licence: Data © OpenStreetMap contributors, ODbL 1.0. "
        "https://osm.org/copyright",
        file=sys.stderr,
    )
    rows: list[dict] = []
    for i, b in enumerate(beaches):
        if i:
            time.sleep(PAUSE_S)
        name = b.get("name") or ""
        concello = b.get("concello") or ""
        try:
            clat = float(b["lat"])
            clon = float(b["lon"])
        except (KeyError, TypeError, ValueError):
            rows.append(
                {
                    "id": b.get("id"),
                    "slug": b.get("slug"),
                    "name": name,
                    "concello": concello,
                    "cat_lat": "",
                    "cat_lon": "",
                    "osm_lat": "",
                    "osm_lon": "",
                    "km": None,
                    "osm_name": "",
                }
            )
            continue
        hit = lookup(name, concello)
        km = (
            haversine_km(clat, clon, hit["lat"], hit["lon"])
            if hit
            else None
        )
        rows.append(
            {
                "id": b.get("id"),
                "slug": b.get("slug"),
                "name": name,
                "concello": concello,
                "cat_lat": f"{clat:.5f}",
                "cat_lon": f"{clon:.5f}",
                "osm_lat": f"{hit['lat']:.5f}" if hit else "",
                "osm_lon": f"{hit['lon']:.5f}" if hit else "",
                "km": km,
                "osm_name": (hit or {}).get("name") or "",
            }
        )
        print(f"lookup {i + 1}/{len(beaches)}", file=sys.stderr, flush=True)
    rows.sort(key=lambda r: (-1.0 if r["km"] is None else -r["km"], r["slug"] or ""))
    print(
        "id\tslug\tname\tconcello\tcat_lat\tcat_lon\tosm_lat\tosm_lon\tkm\tosm_name"
    )
    for r in rows:
        km = "" if r["km"] is None else f"{r['km']:.3f}"
        print(
            f"{r['id']}\t{r['slug']}\t{r['name']}\t{r['concello']}\t"
            f"{r['cat_lat']}\t{r['cat_lon']}\t{r['osm_lat']}\t{r['osm_lon']}\t"
            f"{km}\t{r['osm_name']}"
        )
    missing = sum(1 for r in rows if r["km"] is None)
    print(f"beaches={len(rows)} missing_osm={missing}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
