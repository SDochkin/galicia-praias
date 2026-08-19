#!/usr/bin/env python3
"""Diagnose nearest_wet_cell offset for all beaches (Mar QA). Does not change bake."""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from update_beaches import (  # noqa: E402
    CATALOG,
    CMEMS_SEARCH_RADIUS,
    fetch_copernicus_grid,
    load_json,
    today_utc,
)


def nearest_wet_offset(
    field_2d,
    lats,
    lons,
    lat: float,
    lon: float,
    radius: int,
) -> tuple[int, int, float, float, float] | None:
    """Return (di, dj, cell_lat, cell_lon, t) of nearest wet cell, or None."""
    lat_list = list(lats)
    lon_list = list(lons)
    i = min(range(len(lat_list)), key=lambda k: abs(float(lat_list[k]) - lat))
    j = min(range(len(lon_list)), key=lambda k: abs(float(lon_list[k]) - lon))
    best = None
    best_d2 = None
    best_off = None
    n_i, n_j = len(lat_list), len(lon_list)
    for di in range(-radius, radius + 1):
        for dj in range(-radius, radius + 1):
            ii, jj = i + di, j + dj
            if ii < 0 or jj < 0 or ii >= n_i or jj >= n_j:
                continue
            val = (
                field_2d[ii][jj]
                if not hasattr(field_2d, "shape")
                else field_2d[ii, jj]
            )
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
                best_off = (di, dj, float(lat_list[ii]), float(lon_list[jj]))
    if best is None or best_off is None:
        return None
    di, dj, clat, clon = best_off
    return di, dj, clat, clon, best


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


def main() -> int:
    catalog = load_json(CATALOG)
    beaches = catalog.get("beaches") or []
    if not beaches:
        print("catalog.json missing beaches", file=sys.stderr)
        return 1

    today_d = today_utc()
    today = today_d.isoformat()
    print(f"fetching Copernicus grid for {today}…", flush=True)
    got = fetch_copernicus_grid(today_d)
    if not got:
        print("Copernicus grid unavailable (login / network?)", file=sys.stderr)
        return 2

    grid, lats, lons = got
    field = grid.get(today)
    if field is None:
        print(f"no field for {today}; keys={sorted(grid)[:5]}…", file=sys.stderr)
        return 2

    hist: dict[int, int] = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}
    covered = {0: 0, 1: 0, 2: 0}
    rows: list[dict] = []

    for b in beaches:
        lat = float(b["lat"])
        lon = float(b["lon"])
        for r in (0, 1, 2):
            hit = nearest_wet_offset(field, lats, lons, lat, lon, r)
            if hit is not None:
                covered[r] += 1
        hit2 = nearest_wet_offset(field, lats, lons, lat, lon, CMEMS_SEARCH_RADIUS)
        if hit2 is None:
            continue
        di, dj, clat, clon, _t = hit2
        d2 = di * di + dj * dj
        bucket = min(d2, 4)
        hist[bucket] = hist.get(bucket, 0) + 1
        km = haversine_km(lat, lon, clat, clon)
        rows.append(
            {
                "slug": b["slug"],
                "concello": b["concello"],
                "di": di,
                "dj": dj,
                "d2": d2,
                "km": km,
            }
        )

    n = len(beaches)
    n_ok = len(rows)
    print(f"beaches={n} with_mar_r2={n_ok}")
    print("histogram d2 (radius=2):")
    for k in sorted(hist):
        label = str(k) if k < 4 else "≥4"
        print(f"  d2={label}: {hist[k]} ({100 * hist[k] / max(n_ok, 1):.1f}%)")
    d2s = sorted(r["d2"] for r in rows)
    med = d2s[len(d2s) // 2] if d2s else None
    r2_share = sum(1 for r in rows if r["d2"] == 4) / max(n_ok, 1)
    # plan gate: radius=2 cells means d2 can be up to 4 for corner; "radius=2 срабатывает"
    # means best cell needs the ±2 ring, i.e. d2 > 1 (not in cell and not ±1 only).
    need_r2 = sum(1 for r in rows if r["d2"] > 1) / max(n_ok, 1)
    print(f"median d2={med}")
    print(f"share needing |di| or |dj| == 2 (d2>1): {100 * need_r2:.1f}%")
    print(f"share d2==4 (corner ±2): {100 * r2_share:.1f}%")
    print("coverage by max radius:")
    for r in (0, 1, 2):
        lost = n - covered[r]
        print(
            f"  radius≤{r}: covered={covered[r]} lost={lost} "
            f"({100 * lost / n:.1f}%)"
        )

    worst = sorted(rows, key=lambda r: (-r["km"], -r["d2"], r["slug"]))[:20]
    print("top-20 farthest beach→wet cell:")
    for r in worst:
        print(
            f"  {r['km']:.2f} km  d2={r['d2']}  "
            f"{r['slug']} ({r['concello']})  di={r['di']} dj={r['dj']}"
        )

    if med == 0 and need_r2 < 0.05:
        print(
            "GATE: median d2=0 and <5% need radius=2 ring → "
            "radius/product branch closed; buoy check only."
        )
    else:
        print(
            "GATE: wet-cell offset present or radius=2 often used → "
            "keep radius/product questions open."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
