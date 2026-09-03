#!/usr/bin/env python3
"""Seasonal and live beach layers. Called from update_beaches when weather is skipped."""
from __future__ import annotations

import csv
import html
import io
import json
import math
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent

LAYER_FIELD_KEYS = (
    "flagYear",
    "flagSand",
    "higiene",
    "higieneDate",
    "higieneEcoli",
    "higieneEntero",
    "sinHumo",
    "stopName",
    "parking",
    "toilet",
    "ramp",
    "protectedName",
    "protectedUrl",
    "tidePort",
    "tideLow",
    "tideHigh",
    "tramo",
)
FILTER_FIELD_KEYS = ("flagYear", "sinHumo", "tramo")

FLAG_CSV = (
    "https://abertos.xunta.gal/es/catalogo/cultura-ocio-deporte/"
    "-/dataset/0697/playas-gallegas-con-bandera-azul-2026/001/descarga-directa-del-fichero.csv"
)
SERGAS_PKG = "https://abertos.sergas.gal/api/3/action/package_show?id=mapa-augas-bano-galicia"
SEN_FUME_PAGE = "https://www.sergas.gal/Saude-publica/praiassenfume"
def sen_fume_path(year: int) -> Path:
    return SCRIPTS / f"sen_fume_{year}.txt"
TPGAL_IN_RANGE = "https://tpgal-ws-externos.xunta.gal/tpgal_ws/rest/busstops/in-range"
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
ENP_MAP = (
    "https://ideg.xunta.gal/servizos/rest/services/"
    "LugaresProtexidos/EspazosNaturaisConservacion/MapServer"
)
TIDE_URL = "https://servizos.meteogalicia.gal/mgrss/predicion/mareas/jsonMareas.action"
# JSON_Mareas_es.pdf IdPorto list (15 ports).
TIDE_PORT_IDS = (1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16)
DOG_TRAMO = (
    "https://www.xunta.gal/dog/Publicados/2019/20190412/"
    "AnuncioG0532-040419-0001_gl.html"
)
PAIR_M = 400.0
STOP_M = 400.0
OSM_M = 250.0
UA = {"User-Agent": "galicia-praias/layers"}
# Sample class from this control, not the annual registry grade.
# Units: NMP/100ml on the SERGAS results CSV and mapa-augas page.
HIGIENE_EC_EXCELENTE = 250.0
HIGIENE_EI_EXCELENTE = 100.0
HIGIENE_EC_BUENA = 500.0
HIGIENE_EI_BUENA = 200.0
HIGIENE_EC_LIMITE = 800.0
HIGIENE_EI_LIMITE = 500.0

# Editorial ramp list. Discapnet «Playas accesibles de Galicia»:
# https://www.discapnet.es/ocio/turismo/playas-accesibles-de-galicia
# Only beaches whose entry names a ramp (rampa / acceso mediante rampa),
# plus Samil, O Vao, and Ladeira from that page. Toilet wheelchair is not a ramp.
RAMP_ALLOW = {
    1999,  # A Rapadoira (Foz)
    2010,  # O Portelo (Burela)
    2013,  # A Marosa (Cervo)
    2018,  # O Torno (Cervo)
    2029,  # Area (Viveiro)
    2055,  # Ladeira (Baiona)
    2070,  # O Vao (Vigo)
    2074,  # Samil (Vigo)
    2110,  # Xunqueira (Moaña)
    2119,  # Rodeira (Cangas)
    2133,  # Nerga (Cangas)
    2137,  # Area Brava (Cangas)
    2178,  # Aguete Sur (Marín)
    2180,  # Aguete Norte (Marín)
    2217,  # Silgar (Sanxenxo)
    2448,  # Riazor (A Coruña)
    2456,  # Oza (A Coruña)
}


def _get(url: str, timeout: int = 90, attempts: int = 3) -> bytes:
    last = attempts - 1
    for attempt in range(attempts):
        req = urllib.request.Request(url, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            retry = not isinstance(exc, urllib.error.HTTPError) or exc.code in (
                429,
                502,
                503,
                504,
            )
            if not retry or attempt == last:
                raise
            code = exc.code if isinstance(exc, urllib.error.HTTPError) else "net"
            print(f"layers GET {code} retry {attempt + 1}/{attempts} {url}", flush=True)
            time.sleep(1.0 + attempt)
    raise RuntimeError("unreachable")


def _post(url: str, data: bytes, timeout: int = 180) -> bytes:
    req = urllib.request.Request(
        url,
        data=data,
        headers={**UA, "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    for w in ("praia", "playa", "de", "do", "da", "norte", "sur", "grande", "chica", "pequena"):
        text = re.sub(rf"\b{w}\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def names_agree(a: str, b: str) -> bool:
    na, nb = fold(a), fold(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= 0.7


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def decode_csv(body: bytes) -> list[dict[str, str]]:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = body.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = body.decode("utf-8", errors="replace")
    sample = text[:4000]
    dialect = csv.Sniffer().sniff(sample, delimiters=";,|\t")
    return list(csv.DictReader(io.StringIO(text), dialect=dialect))


def parse_float(v: Any) -> float | None:
    if v is None:
        return None
    s = str(v).strip().replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def pair_rows(rows: list[dict], beaches: list[dict], pair_m: float = PAIR_M) -> dict[int, dict]:
    """Match source rows with name/concello/lat/lon. Skip if the pair is doubtful."""
    used: dict[int, dict] = {}
    claimed: set[int] = set()
    by_conc: dict[str, list[dict]] = {}
    for b in beaches:
        by_conc.setdefault(fold(b["concello"]), []).append(b)

    for row in rows:
        cands = by_conc.get(fold(str(row.get("concello") or "")), [])
        name_hits = [b for b in cands if names_agree(b["name"], str(row.get("name") or ""))]
        if len(name_hits) > 1:
            continue
        if len(name_hits) == 1:
            bid = name_hits[0]["id"]
            if bid in claimed:
                used.pop(bid, None)
                continue
            claimed.add(bid)
            used[bid] = row
            continue
        lat, lon = row.get("lat"), row.get("lon")
        if not isinstance(lat, float) or not isinstance(lon, float):
            continue
        ranked = sorted(
            ((haversine_m(lat, lon, float(b["lat"]), float(b["lon"])), b) for b in beaches),
            key=lambda x: x[0],
        )
        if not ranked:
            continue
        d0, b0 = ranked[0]
        d1 = ranked[1][0] if len(ranked) > 1 else 1e9
        if d0 <= pair_m and d1 >= max(d0 * 2, pair_m) and b0["id"] not in claimed:
            claimed.add(b0["id"])
            used[b0["id"]] = row
    return used


def apply_layer_fields(
    beach: dict,
    prev: dict | None,
    replaced: set[str],
    values: dict[str, Any],
) -> None:
    for k in LAYER_FIELD_KEYS:
        if k in replaced:
            if k in values:
                beach[k] = values[k]
        elif prev and k in prev:
            beach[k] = prev[k]


def copy_layer_fields(dst: dict, src: dict | None, keys: tuple[str, ...] = LAYER_FIELD_KEYS) -> None:
    if not src:
        return
    for k in keys:
        if k in src:
            dst[k] = src[k]


def _col(row: dict, *needles: str) -> str:
    for key, val in row.items():
        lk = fold(key or "")
        if any(n in lk for n in needles):
            return str(val or "").strip()
    return ""


def fetch_flag(beaches: list[dict], year: int) -> dict[int, dict] | None:
    try:
        rows_raw = decode_csv(_get(FLAG_CSV))
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        print(f"layers flag fail: {exc}", flush=True)
        return None
    rows = []
    for r in rows_raw:
        name = (r.get("PRAIA") or "").strip()
        conc = (r.get("CONCELLO") or "").strip()
        if not name or not conc:
            continue
        lat = lon = None
        coords = (r.get("COORDENADAS ") or r.get("COORDENADAS") or "").strip()
        m = re.match(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)", coords)
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
        sand = (r.get("TIPO DE AREA") or r.get("TIPO DE AREA ") or "").strip()
        rows.append({"name": name, "concello": conc, "lat": lat, "lon": lon, "sand": sand})
    paired = pair_rows(rows, beaches)
    out: dict[int, dict] = {}
    for bid, row in paired.items():
        rec: dict[str, Any] = {"flagYear": year}
        if row.get("sand"):
            rec["flagSand"] = row["sand"]
        out[bid] = rec
    print(f"layers flag src={len(rows)} paired={len(out)}", flush=True)
    return out


def _latest_resource(resources: list[dict], suffix: str) -> dict | None:
    hits = [r for r in resources if str(r.get("url") or "").endswith(suffix)]
    if not hits:
        return None
    return max(hits, key=lambda r: r.get("last_modified") or r.get("created") or "")


def _parse_nmp(raw: str) -> float | None:
    s = (raw or "").strip().replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if not m:
        return None
    return float(m.group(1))


def _higiene_score(ec: float | None, ei: float | None) -> int | None:
    if ec is None or ei is None:
        return None
    ec_v = ec
    ei_v = ei
    if ec_v > HIGIENE_EC_LIMITE or ei_v > HIGIENE_EI_LIMITE:
        return 15
    if ec_v <= HIGIENE_EC_EXCELENTE and ei_v <= HIGIENE_EI_EXCELENTE:
        return 100
    if ec_v <= HIGIENE_EC_BUENA and ei_v <= HIGIENE_EI_BUENA:
        return 75
    return 40


def fetch_higiene(beaches: list[dict]) -> dict[int, dict] | None:
    pkg = json.loads(_get(SERGAS_PKG).decode("utf-8"))
    resources = pkg["result"]["resources"]
    reg_r = _latest_resource(resources, "rexistro-augas-bano-galicia-campana.csv")
    res_r = _latest_resource(resources, "resultados-analiticos-rabg-campana.csv")
    if not reg_r or not res_r:
        print("layers higiene: missing CSV", flush=True)
        return None
    registry = decode_csv(_get(reg_r["url"]))
    results = decode_csv(_get(res_r["url"]))
    latest: dict[str, dict] = {}
    for r in results:
        rabg = (r.get("Código RABG") or "").strip()
        if not rabg:
            continue
        date = (r.get("Data do último control") or "").strip()
        prev = latest.get(rabg)
        if prev is None or date > (prev.get("Data do último control") or ""):
            latest[rabg] = r
    rows = []
    for r in registry:
        rabg = (r.get("Código RABG") or "").strip()
        name = (r.get("Zona de baño") or r.get("Código da zona") or "").strip()
        conc = (r.get("Concello") or "").strip()
        if not rabg or not name:
            continue
        lon = parse_float(r.get("Coordenada X"))
        lat = parse_float(r.get("Coordenada Y"))
        rows.append({"name": name, "concello": conc, "lat": lat, "lon": lon, "rabg": rabg})
    paired = pair_rows(rows, beaches)
    out: dict[int, dict] = {}
    for bid, row in paired.items():
        sample = latest.get(row["rabg"])
        if not sample:
            continue
        ecoli = (sample.get("Escherichia coli") or "").strip()
        entero = (sample.get("Enterococo intestinal") or "").strip()
        date = (sample.get("Data do último control") or "").strip()[:10]
        score = _higiene_score(_parse_nmp(ecoli), _parse_nmp(entero))
        if score is None or not date:
            continue
        rec: dict[str, Any] = {"higiene": score, "higieneDate": date}
        if ecoli:
            rec["higieneEcoli"] = ecoli
        if entero:
            rec["higieneEntero"] = entero
        out[bid] = rec
    print(
        f"layers higiene registry={len(rows)} rabg={len(latest)} paired={len(paired)} scored={len(out)}",
        flush=True,
    )
    return out


def load_sen_fume_rows(year: int) -> list[dict]:
    path = sen_fume_path(year)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        rows.append({"name": parts[1].strip(), "concello": parts[0].strip(), "lat": None, "lon": None})
    return rows


def fetch_sin_humo(beaches: list[dict], year: int) -> dict[int, dict] | None:
    rows = load_sen_fume_rows(year)
    if not rows:
        return None
    paired = pair_rows(rows, beaches)
    out = {bid: {"sinHumo": True} for bid in paired}
    print(f"layers sinHumo src={len(rows)} paired={len(out)} page={SEN_FUME_PAGE}", flush=True)
    return out


def _parse_tpgal_stops(payload: Any) -> list[dict]:
    if isinstance(payload, dict):
        items = payload.get("results") or payload.get("data") or payload.get("busstops") or []
    elif isinstance(payload, list):
        items = payload
    else:
        return []
    out = []
    for el in items:
        if not isinstance(el, dict):
            continue
        name = el.get("text") or el.get("name") or el.get("stop_name") or ""
        loc = el.get("location") or {}
        lat = parse_float(el.get("latitude") or loc.get("latitude"))
        lon = parse_float(el.get("longitude") or loc.get("longitude"))
        if isinstance(lat, float) and isinstance(lon, float):
            out.append({"name": str(name), "lat": lat, "lon": lon})
    return out


def fetch_stops(beaches: list[dict]) -> dict[int, dict] | None:
    stops: list[dict] = []
    seen: set[tuple[float, float, str]] = set()
    lat0, lat1, lon0, lon1 = 41.8, 43.8, -9.3, -6.8
    tiles = ok = 0
    lat = lat0
    while lat <= lat1:
        lon = lon0
        while lon <= lon1:
            tiles += 1
            try:
                q = urllib.parse.urlencode(
                    {"latitude": f"{lat:.3f}", "longitude": f"{lon:.3f}", "range": 30}
                )
                body = _get(f"{TPGAL_IN_RANGE}?{q}", timeout=60)
                for s in _parse_tpgal_stops(json.loads(body.decode("utf-8"))):
                    key = (round(s["lat"], 5), round(s["lon"], 5), s["name"])
                    if key in seen:
                        continue
                    seen.add(key)
                    stops.append(s)
                ok += 1
            except Exception as exc:  # noqa: BLE001
                print(f"layers stops tile {lat:.2f},{lon:.2f} fail: {exc}", flush=True)
            lon += 0.35
        lat += 0.25
    if tiles == 0 or ok != tiles:
        print(f"layers stops incomplete ok={ok}/{tiles}", flush=True)
        return None
    out: dict[int, dict] = {}
    if not stops:
        print("layers stops src=0", flush=True)
        return out
    for b in beaches:
        ranked = sorted(
            (
                (haversine_m(float(b["lat"]), float(b["lon"]), s["lat"], s["lon"]), s)
                for s in stops
            ),
            key=lambda x: x[0],
        )
        if ranked and ranked[0][0] <= STOP_M:
            out[b["id"]] = {"stopName": ranked[0][1]["name"] or "—"}
    print(f"layers stops src={len(stops)} hit={len(out)}", flush=True)
    return out


def _osm_points(elements: list[dict], amenity: str | None = None) -> list[tuple[float, float]]:
    pts = []
    for el in elements:
        tags = el.get("tags") or {}
        if amenity and tags.get("amenity") != amenity:
            continue
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            pts.append((float(lat), float(lon)))
    return pts


def fetch_osm(beaches: list[dict]) -> dict[int, dict] | None:
    elements: list[dict] = []
    lat0, lat1, lon0, lon1 = 41.7, 43.9, -9.4, -6.7
    step_lat, step_lon = 0.55, 0.68
    tiles = ok = 0
    lat = lat0
    while lat < lat1 - 1e-6:
        north = min(lat + step_lat, lat1)
        lon = lon0
        while lon < lon1 - 1e-6:
            east = min(lon + step_lon, lon1)
            tiles += 1
            query = (
                "[out:json][timeout:90];"
                "("
                f'nwr["amenity"="parking"]({lat:.3f},{lon:.3f},{north:.3f},{east:.3f});'
                f'nwr["amenity"="toilets"]({lat:.3f},{lon:.3f},{north:.3f},{east:.3f});'
                ");out center;"
            )
            payload = urllib.parse.urlencode({"data": query}).encode("utf-8")
            got = False
            last_exc: Exception | None = None
            for url in OVERPASS_URLS:
                try:
                    body = _post(url, payload, timeout=120)
                    elements.extend(json.loads(body.decode("utf-8")).get("elements") or [])
                    got = True
                    break
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    time.sleep(2)
            if got:
                ok += 1
            else:
                print(f"layers osm tile {lat:.2f},{lon:.2f} fail: {last_exc}", flush=True)
            lon = east
        lat = north
    if tiles == 0 or ok != tiles:
        print(f"layers osm incomplete ok={ok}/{tiles}", flush=True)
        return None
    parking = _osm_points(elements, amenity="parking")
    toilets = _osm_points(elements, amenity="toilets")
    out: dict[int, dict] = {}
    for b in beaches:
        rec: dict[str, Any] = {}
        blat, blon = float(b["lat"]), float(b["lon"])
        if any(haversine_m(blat, blon, lat, lon) <= OSM_M for lat, lon in parking):
            rec["parking"] = True
        if any(haversine_m(blat, blon, lat, lon) <= OSM_M for lat, lon in toilets):
            rec["toilet"] = True
        if rec:
            out[b["id"]] = rec
    print(
        f"layers osm parking={sum(1 for v in out.values() if v.get('parking'))} "
        f"toilet={sum(1 for v in out.values() if v.get('toilet'))}",
        flush=True,
    )
    return out


def fetch_ramp_allow(beaches: list[dict]) -> dict[int, dict]:
    known = {b["id"] for b in beaches}
    missing = sorted(bid for bid in RAMP_ALLOW if bid not in known)
    if missing:
        print(f"layers RAMP_ALLOW missing {missing}", flush=True)
    out = {bid: {"ramp": True} for bid in RAMP_ALLOW if bid in known}
    print(f"layers ramp editorial={len(out)}", flush=True)
    return out


def _ring_contains(ring: list, lat: float, lon: float) -> bool:
    inside = False
    n = len(ring)
    if n < 4:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = float(ring[i][0]), float(ring[i][1])
        xj, yj = float(ring[j][0]), float(ring[j][1])
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-18) + xi:
            inside = not inside
        j = i
    return inside


def _geom_contains(geom: dict, lat: float, lon: float) -> bool:
    gtype = geom.get("type")
    coords = geom.get("coordinates") or []
    if gtype == "Polygon":
        if not coords or not _ring_contains(coords[0], lat, lon):
            return False
        return all(not _ring_contains(hole, lat, lon) for hole in coords[1:])
    if gtype == "MultiPolygon":
        return any(_geom_contains({"type": "Polygon", "coordinates": poly}, lat, lon) for poly in coords)
    return False


def _feature_name(props: dict) -> str:
    for key in ("SITE_NAME", "NOME", "NAME", "Texto", "nome", "name"):
        val = props.get(key)
        if val:
            return str(val)
    return ""


def _enp_features(lid: int) -> list[dict]:
    ids_url = f"{ENP_MAP}/{lid}/query?where=1%3D1&returnIdsOnly=true&f=json"
    payload = json.loads(_get(ids_url, timeout=60).decode("utf-8"))
    oids = payload.get("objectIds") or []
    feats: list[dict] = []
    step = 3
    for i in range(0, len(oids), step):
        chunk = ",".join(str(x) for x in oids[i : i + step])
        url = (
            f"{ENP_MAP}/{lid}/query?objectIds={chunk}&outFields=*"
            f"&returnGeometry=true&outSR=4326&f=geojson"
        )
        data = json.loads(_get(url, timeout=120).decode("utf-8"))
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        for feat in data.get("features") or []:
            if feat.get("geometry"):
                feats.append(feat)
    return feats


def fetch_protected(beaches: list[dict]) -> dict[int, dict] | None:
    # Parks first, then Natura. No URL field on these layers → no Ficha.
    layers = (5, 6, 7, 9, 10, 2, 1)
    feats: list[tuple[int, dict]] = []
    for lid in layers:
        try:
            for feat in _enp_features(lid):
                feats.append((lid, feat))
        except Exception as exc:  # noqa: BLE001
            print(f"layers protected layer {lid} fail: {exc}", flush=True)
            return None
    out: dict[int, dict] = {}
    for b in beaches:
        lat, lon = float(b["lat"]), float(b["lon"])
        hit = None
        for _lid, feat in feats:
            if _geom_contains(feat.get("geometry") or {}, lat, lon):
                hit = feat
                break
        if not hit:
            continue
        name = _feature_name(hit.get("properties") or {})
        if name:
            out[b["id"]] = {"protectedName": name}
    print(f"layers protected polys={len(feats)} hit={len(out)}", flush=True)
    return out


def fetch_tides(beaches: list[dict]) -> dict[int, dict] | None:
    ports: list[dict] = []
    for pid in TIDE_PORT_IDS:
        try:
            body = _get(f"{TIDE_URL}?idPorto={pid}", timeout=45)
            js = json.loads(body.decode("utf-8"))
            rec = (js.get("mareas") or [None])[0]
        except Exception as exc:  # noqa: BLE001
            print(f"layers tide {pid} fail: {exc}", flush=True)
            continue
        if not rec:
            continue
        lat, lon = parse_float(rec.get("latitude")), parse_float(rec.get("lonxitude"))
        if lat is None or lon is None:
            continue
        low = [m.get("hora") for m in rec.get("listaMareas") or [] if m.get("tipoMarea") == "Baixamar" and m.get("hora")]
        high = [m.get("hora") for m in rec.get("listaMareas") or [] if m.get("tipoMarea") == "Preamar" and m.get("hora")]
        ports.append(
            {
                "name": rec.get("nomePorto") or str(pid),
                "lat": lat,
                "lon": lon,
                "low": low,
                "high": high,
            }
        )
    if not ports:
        print("layers tide ports=0", flush=True)
        return None
    out: dict[int, dict] = {}
    for b in beaches:
        if not ports:
            break
        port = min(ports, key=lambda p: haversine_m(float(b["lat"]), float(b["lon"]), p["lat"], p["lon"]))
        rec: dict[str, Any] = {"tidePort": port["name"]}
        if port["low"]:
            rec["tideLow"] = port["low"]
        if port["high"]:
            rec["tideHigh"] = port["high"]
        out[b["id"]] = rec
    print(f"layers tide ports={len(ports)} beaches={len(out)}", flush=True)
    return out


def utm29n_to_wgs84(easting: float, northing: float) -> tuple[float, float]:
    # WGS84 UTM zone 29N.
    k0 = 0.9996
    a = 6378137.0
    e2 = 6.69437999014e-3
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    x = easting - 500000.0
    y = northing
    m = y / k0
    mu = m / (a * (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256))
    phi1 = (
        mu
        + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
        + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
        + (151 * e1**3 / 96) * math.sin(6 * mu)
    )
    sinp = math.sin(phi1)
    cosp = math.cos(phi1)
    tanp = math.tan(phi1)
    e2p = e2 / (1 - e2)
    n = a / math.sqrt(1 - e2 * sinp**2)
    t = tanp**2
    c = e2p * cosp**2
    r = a * (1 - e2) / (1 - e2 * sinp**2) ** 1.5
    d = x / (n * k0)
    lat = phi1 - (n * tanp / r) * (
        d**2 / 2
        - (5 + 3 * t + 10 * c - 4 * c**2 - 9 * e2p) * d**4 / 24
        + (61 + 90 * t + 298 * c + 45 * t**2 - 252 * e2p - 3 * c**2) * d**6 / 720
    )
    lon0 = math.radians(-9.0)
    lon = lon0 + (
        d
        - (1 + 2 * t + c) * d**3 / 6
        + (5 - 2 * c + 28 * t - 3 * c**2 + 8 * e2p + 24 * t**2) * d**5 / 120
    ) / cosp
    return math.degrees(lat), math.degrees(lon)


def parse_tramo_html(raw: str) -> list[dict]:
    text = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    lines = [html.unescape(l).strip() for l in text.splitlines()]
    lines = [l for l in lines if l]
    recs: list[dict] = []
    for i, line in enumerate(lines):
        cat = line.replace("\xa0", " ").strip()
        if cat not in {"Urbana", "Natural", "Urbana *"}:
            continue
        if i < 3 or i + 3 >= len(lines):
            continue
        name = lines[i - 2]
        conc = lines[i - 3]
        x = parse_float(lines[i + 2])
        y = parse_float(lines[i + 3])
        lat = lon = None
        if x is not None and y is not None and x > 1000:
            lat, lon = utm29n_to_wgs84(x, y)
        kind = "urbano" if cat.startswith("Urbana") else "natural"
        recs.append({"name": name, "concello": conc, "lat": lat, "lon": lon, "tramo": kind})
    return recs


def fetch_tramo(beaches: list[dict]) -> dict[int, dict]:
    recs = parse_tramo_html(_get(DOG_TRAMO, timeout=90).decode("utf-8", errors="replace"))
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in recs:
        groups.setdefault((fold(r["concello"]), fold(r["name"])), []).append(r)
    rows = []
    mixed: list[list[dict]] = []
    for items in groups.values():
        kinds = {i["tramo"] for i in items}
        if len(kinds) == 1:
            rows.append(dict(items[0]))
        else:
            mixed.append(items)
    paired = pair_rows(rows, beaches)
    out = {bid: {"tramo": row["tramo"]} for bid, row in paired.items() if row.get("tramo")}
    claimed = set(out)
    for items in mixed:
        name = items[0]["name"]
        conc = items[0]["concello"]
        cands = [
            b
            for b in beaches
            if fold(b["concello"]) == fold(conc) and names_agree(b["name"], name)
        ]
        if len(cands) != 1:
            continue
        b0 = cands[0]
        if b0["id"] in claimed:
            continue
        ranked = []
        for item in items:
            if not isinstance(item.get("lat"), float) or not isinstance(item.get("lon"), float):
                continue
            ranked.append(
                (
                    haversine_m(float(b0["lat"]), float(b0["lon"]), item["lat"], item["lon"]),
                    item,
                )
            )
        if not ranked:
            continue
        ranked.sort(key=lambda x: x[0])
        out[b0["id"]] = {"tramo": ranked[0][1]["tramo"]}
        claimed.add(b0["id"])
    print(f"layers tramo src={len(recs)} groups={len(groups)} paired={len(out)}", flush=True)
    return out


def season_already_baked(prev_by_id: dict[int, dict], year: int) -> bool:
    return any(p.get("flagYear") == year for p in prev_by_id.values())


def static_already_baked(prev_by_id: dict[int, dict], key: str) -> bool:
    return any(key in p for p in prev_by_id.values())


def collect_layers(
    beaches: list[dict],
    prev_by_id: dict[int, dict],
    year: int,
) -> tuple[dict[int, dict], set[str]]:
    """Return beach_id → layer values and the set of keys this step replaced."""
    replaced: set[str] = set()
    by_id: dict[int, dict] = {b["id"]: {} for b in beaches}

    def merge(got: dict[int, dict] | None, keys: tuple[str, ...]) -> None:
        if got is None:
            print(f"layers keep prev {','.join(keys)}", flush=True)
            return
        replaced.update(keys)
        for bid, rec in got.items():
            by_id.setdefault(bid, {}).update(rec)

    if not season_already_baked(prev_by_id, year):
        merge(fetch_flag(beaches, year), ("flagYear", "flagSand"))
        merge(fetch_sin_humo(beaches, year), ("sinHumo",))
    else:
        print(f"layers seasonal {year} already present; skip flag/sinHumo", flush=True)

    merge(fetch_higiene(beaches), ("higiene", "higieneDate", "higieneEcoli", "higieneEntero"))
    merge(fetch_tides(beaches), ("tidePort", "tideLow", "tideHigh"))
    merge(fetch_stops(beaches), ("stopName",))
    merge(fetch_osm(beaches), ("parking", "toilet"))
    merge(fetch_ramp_allow(beaches), ("ramp",))
    if not static_already_baked(prev_by_id, "protectedName"):
        merge(fetch_protected(beaches), ("protectedName", "protectedUrl"))
    else:
        print("layers protected already present; skip", flush=True)
    if not static_already_baked(prev_by_id, "tramo"):
        merge(fetch_tramo(beaches), ("tramo",))
    else:
        print("layers tramo already present; skip", flush=True)
    return by_id, replaced


def run_selfcheck() -> None:
    assert names_agree("Praia de Samil", "Samil")
    assert names_agree("Praia América", "Playa America")
    assert not names_agree("Samil", "Silgar")

    beaches = [
        {"id": 1, "name": "Samil", "concello": "Vigo", "lat": 42.21, "lon": -8.77},
        {"id": 2, "name": "Silgar", "concello": "Sanxenxo", "lat": 42.40, "lon": -8.81},
    ]
    paired = pair_rows(
        [{"name": "Samil", "concello": "Vigo", "lat": None, "lon": None}],
        beaches,
    )
    assert paired[1]["name"] == "Samil"
    assert 2 not in paired
    ambig = pair_rows(
        [{"name": "Praia", "concello": "Vigo", "lat": None, "lon": None}],
        [
            {"id": 1, "name": "Praia de Samil", "concello": "Vigo", "lat": 42.21, "lon": -8.77},
            {"id": 3, "name": "Praia do Vao", "concello": "Vigo", "lat": 42.20, "lon": -8.78},
        ],
    )
    assert ambig == {}

    assert _higiene_score(None, None) is None
    assert _higiene_score(10.0, None) is None
    assert _higiene_score(None, 10.0) is None
    assert _higiene_score(10.0, 10.0) == 100
    assert _higiene_score(HIGIENE_EC_LIMITE + 1, 10.0) == 15

    lat, lon = utm29n_to_wgs84(537000.0, 4743000.0)
    assert 42.7 < lat < 43.1 and -8.8 < lon < -8.3

    square = {
        "type": "Polygon",
        "coordinates": [[[-9.0, 42.0], [-8.0, 42.0], [-8.0, 43.0], [-9.0, 43.0], [-9.0, 42.0]]],
    }
    assert _geom_contains(square, 42.5, -8.5)
    assert not _geom_contains(square, 41.0, -8.5)

    html_frag = (
        "<p>Vigo</p><p>Samil</p><p>foo</p><p>Urbana</p>"
        "<p>bar</p><p>521000</p><p>4678000</p><p>extra</p>"
    )
    recs = parse_tramo_html(html_frag)
    assert len(recs) == 1
    assert recs[0]["name"] == "Samil" and recs[0]["concello"] == "Vigo"
    assert recs[0]["tramo"] == "urbano"
    assert isinstance(recs[0]["lat"], float) and isinstance(recs[0]["lon"], float)

    assert sen_fume_path(2027).name == "sen_fume_2027.txt"
    assert fetch_sin_humo(beaches, 1999) is None
