#!/usr/bin/env python3
"""Does OSM know this beach name in this concello? Does not write catalog or data/.

This script does not judge geotags. Nominatim returns one label point.
Distance from that point to catalog.json is not a miss: the same beach
has a Google POI, a catalog pin, and an OSM node in different places.

A row is missing when no natural=beach in the concello has the same
name_key as the catalog. name_key: casefold, strip accents, drop
praia/playa/de/da/do/d/as/os/a/o, compare the whole remainder.
Same name + concello: one OSM label goes to the nearest catalog row.

Evidence 2026-08-23:
- Policy: https://operations.osmfoundation.org/policies/nominatim/
  max 1 request/s; valid User-Agent; one-time bulk is single-thread.
- Live GET (User-Agent galicia-praias/diag_geotag):
  https://nominatim.openstreetmap.org/search?q=Praia+do+Cabo+Redondela&format=json&limit=3
  HTTP 200. First hit: lat 42.3098872 lon -8.6238043 (degrees WGS84),
  class=natural type=beach name=O Vao do Cabo.

Point or name fixes go through NAME_OVERRIDES in scripts/build_catalog.py
and only when the owner names specific ids.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import unicodedata
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


def nominatim_search(q: str) -> list[dict]:
    params = {
        "q": q,
        "format": "json",
        "limit": "3",
        "countrycodes": "es",
        "addressdetails": "1",
    }
    url = NOMINATIM + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=45) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if not isinstance(body, list):
        return []
    out: list[dict] = []
    for hit in body:
        if not isinstance(hit, dict):
            continue
        if hit.get("class") != "natural" or hit.get("type") != "beach":
            continue
        try:
            lat = float(hit["lat"])
            lon = float(hit["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        addr = hit.get("address") if isinstance(hit.get("address"), dict) else {}
        display = hit.get("display_name") or hit.get("name") or ""
        short = addr.get("beach") or hit.get("name") or display.split(",")[0]
        out.append(
            {
                "lat": lat,
                "lon": lon,
                "name": display,
                "short": str(short),
                "address": addr,
            }
        )
    return out


def name_key(raw: str) -> str:
    s = unicodedata.normalize("NFD", raw or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.casefold()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\b(praia|playa|de|da|do|d|as|os|a|o)\b", " ", s)
    return " ".join(s.split())


def same_beach_name(osm_name: str, catalog_name: str) -> bool:
    a, b = name_key(osm_name), name_key(catalog_name)
    return bool(a) and a == b


def _place_key(raw: str) -> str:
    s = (raw or "").casefold().strip()
    for prefix in ("as ", "os ", "a ", "o "):
        if s.startswith(prefix):
            s = s[len(prefix) :]
            break
    return s


def same_concello(hit: dict, concello: str) -> bool:
    want = _place_key(concello)
    if not want:
        return False
    addr = hit.get("address") or {}
    fields = (
        addr.get("municipality"),
        addr.get("city"),
        addr.get("town"),
        addr.get("village"),
        addr.get("city_district"),
    )
    return any(_place_key(str(v)) == want for v in fields if v)


def drop_sibling_osm(rows: list[dict]) -> None:
    """Give each OSM point to the nearest same-name catalog row in the concello."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        if r.get("km") is None or not r.get("osm_lat"):
            continue
        osm_short = (r.get("osm_name") or "").split(",")[0]
        if not same_beach_name(osm_short, r.get("name") or ""):
            continue
        key = (name_key(r.get("name") or ""), r.get("concello") or "")
        groups.setdefault(key, []).append(r)
    for members in groups.values():
        members.sort(key=lambda r: float(r["km"]))
        taken_osm: set[tuple[str, str]] = set()
        keep_ids: set = set()
        for r in members:
            osm = (str(r["osm_lat"]), str(r["osm_lon"]))
            if r["id"] in keep_ids or osm in taken_osm:
                continue
            keep_ids.add(r["id"])
            taken_osm.add(osm)
        for r in members:
            if r["id"] in keep_ids:
                continue
            r["km"] = None
            r["osm_lat"] = ""
            r["osm_lon"] = ""
            r["osm_name"] = ""


def lookup(name: str, concello: str) -> dict | None:
    queries = [
        f"{name} {concello}",
        f"{name}, {concello}, Galicia, España",
    ]
    for i, q in enumerate(queries):
        if i:
            time.sleep(PAUSE_S)
        try:
            hits = nominatim_search(q)
        except Exception as exc:  # noqa: BLE001
            print(f"WARN {q}: {exc}", file=sys.stderr)
            continue
        for hit in hits:
            if same_concello(hit, concello) and same_beach_name(
                hit.get("short") or "", name
            ):
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
    drop_sibling_osm(rows)
    rows.sort(key=lambda r: (r.get("concello") or "", r.get("name") or "", r.get("slug") or ""))
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
    print(
        f"beaches={len(rows)} osm_has_name={len(rows) - missing} osm_no_name={missing}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
