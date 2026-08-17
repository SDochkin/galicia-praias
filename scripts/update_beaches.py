#!/usr/bin/env python3
"""Daily bake of beaches.json from MeteoGalicia + AEMET."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog.json"
BEACHES = ROOT / "beaches.json"
MG_URL = "https://servizos.meteogalicia.gal/mgrss/predicion/jsonPredPraia.action?idPraia={}"
AEMET_URL = "https://opendata.aemet.es/opendata/api/prediccion/especifica/playa/{}"


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
                    return None
                raw = d["dataPredicion"]
                day = raw[:10] if isinstance(raw, str) else str(raw)[:10]
                out.append({"date": day, "t": int(t)})
            if len(out) < 1:
                return None
            while len(out) < 3 and out:
                out.append(out[-1])
            return out[:3]
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
                out.append({"date": day, "t": int(t)})
            if not out:
                return None
            while len(out) < 3:
                out.append(out[-1])
            return out[:3]
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


def previous_source(prev: dict, beach_id: int, source_name: str) -> dict | None:
    for b in prev.get("beaches", []):
        if b.get("id") == beach_id:
            for s in b.get("sources", []):
                if s.get("name") == source_name and s.get("days"):
                    return s
    return None


def source_fresh(src: dict, max_age_days: int = 3) -> bool:
    try:
        d0 = date.fromisoformat(src["days"][0]["date"])
    except (KeyError, IndexError, ValueError):
        return False
    return (today_utc() - d0).days <= max_age_days


def main() -> int:
    catalog = load_json(CATALOG)
    if not catalog.get("beaches"):
        print("catalog.json missing beaches", file=sys.stderr)
        return 1

    prev = load_json(BEACHES)
    api_key = os.environ.get("AEMET_API_KEY", "").strip()
    today = today_utc().isoformat()

    beaches_out = []
    fresh_mg = 0
    cat_ids = []

    for entry in catalog["beaches"]:
        bid = entry["id"]
        cat_ids.append(bid)
        sources: list[dict] = []

        mg_days = fetch_mg(bid)
        time.sleep(0.3)
        if mg_days and mg_days[0]["date"] == today:
            fresh_mg += 1
            sources.append({"name": "MeteoGalicia", "days": mg_days})
        else:
            old = previous_source(prev, bid, "MeteoGalicia")
            if old and source_fresh(old):
                sources.append(old)

        aemet_id = entry.get("aemetId")
        if aemet_id and api_key:
            ae_days = fetch_aemet(str(aemet_id), api_key)
            time.sleep(2.0)
            if ae_days and ae_days[0]["date"] == today:
                sources.append({"name": "AEMET", "days": ae_days})
            else:
                old = previous_source(prev, bid, "AEMET")
                if old and source_fresh(old):
                    sources.append(old)
        elif aemet_id and not api_key:
            old = previous_source(prev, bid, "AEMET")
            if old and source_fresh(old):
                sources.append(old)

        # drop stale sources already filtered; keep structure
        sources = [s for s in sources if source_fresh(s)]

        beaches_out.append(
            {
                "id": bid,
                "slug": entry["slug"],
                "name": entry["name"],
                "concello": entry["concello"],
                "concelloSlug": entry["concelloSlug"],
                "sources": sources,
            }
        )

    # guards
    if fresh_mg < len(catalog["beaches"]) / 2:
        print(
            f"ABORT: fresh MG {fresh_mg}/{len(catalog['beaches'])} < 50%",
            file=sys.stderr,
        )
        return 2

    out_ids = [b["id"] for b in beaches_out]
    if sorted(out_ids) != sorted(cat_ids):
        print("ABORT: id set != catalog", file=sys.stderr)
        return 3

    payload = {
        "fetchedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "beaches": beaches_out,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    json.loads(text)  # parse guard
    BEACHES.write_text(text, encoding="utf-8")
    print(f"wrote beaches.json fresh_mg={fresh_mg} beaches={len(beaches_out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
