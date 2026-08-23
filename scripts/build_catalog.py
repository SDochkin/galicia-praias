#!/usr/bin/env python3
"""One-shot: parse MeteoGalicia annex + AEMET CSV → catalog.json + NOTES.md."""

from __future__ import annotations

import json
import math
import re
import time
import unicodedata
import urllib.request
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PDF_URL = "https://www.meteogalicia.gal/datosred/infoweb/meteo/docs/rss/JSON_Pred_Praia_es.pdf"
CSV_URL = "https://www.aemet.es/documentos/es/eltiempo/prediccion/playas/Playas_codigos.csv"
MG_URL = "https://servizos.meteogalicia.gal/mgrss/predicion/jsonPredPraia.action?idPraia={}"
CACHE = Path("/tmp/galicia-praias-build")
GALICIA_PROV = {"15", "27", "36"}

# Annex puts these under wrong concello header (plan: Pontevedra 17/18 swap).
FORCE_CONCELLO = {
    2095: "Vilaboa",
    2096: "Vilaboa",
    2099: "Vilaboa",
}

# Designated dog beaches from Turismo de Galicia blog, matched by id + concello.
DOGS_ALLOW = {2498, 2240, 2089, 2295, 2073}

DOGS_UNMATCHED = (
    "Areal (Pobra do Caramiñal) — no catalog match by id+concello",
    "Punta Corveira (Barreiros) — no catalog match by id+concello",
    "O Portiño (O Grove) — no catalog match by id+concello",
    "Cunchiña / Cheminea / Massó (Cangas) — no catalog match by id+concello",
    "Arealonga (Redondela) — no catalog match by id+concello",
    "A Foz (Vigo) — no catalog match by id+concello",
)

# Owner-named id → (lat, lon) WGS84 degrees. Applied after MG fetch.
COORD_OVERRIDES: dict[int, tuple[float, float]] = {
    2446: (43.3724, -8.41866),  # Lino; was swapped with 2447
    2447: (43.374, -8.41943),  # San Roque; was swapped with 2446
}

# Map-facing names (OSM / local spelling). Slug is recomputed from the new name.
NAME_OVERRIDES: dict[int, str] = {
    2453: "Praia de Durmideiras",  # was Adormideiras; OSM
    2380: "Praia da Covasa",  # was A Cobasa; OSM
    1979: "Praia de Rochas Brancas",  # was Rocas Blancas; OSM
    1936: "Praia das Maceiras",  # was Masteiras; OSM
    1856: "Praia de Ximprón",  # was Simprón; OSM
    2141: "Praia do Estripeiro",  # was Estrepeiros; OSM
    2262: "Praia das Lousiñas (Sur)",  # was Lauxiñas; OSM
    2527: "Praia de Bidueiro",  # was Vidueiros; OSM
}

# Only documented same-beach aliases (applied after names_agree fails).
AEMET_FORCE_PAIRS: dict[int, str] = {}

# Mutual-nearest ≤400 m but different beaches — keep unpaired with reason.
AEMET_REJECT_REASONS: dict[int, str] = {
    1971: "near Caión but different beach (Salseiras)",
    2345: "near Mañóns but different beach (Carragueiros)",
    1959: "near Maior/Malpica but different beach (Canido)",
    2518: "near Bares but different beach (Vares Oeste)",
    2421: "near Esteiro but different beach (Portiño)",
    2526: "near Espasante but different beach (A Concha)",
    2360: "near Areal but different beach (Caramiñal)",
    2363: "near Cabío-Lombiña but different beach (Nineiriños)",
    2394: "near As Furnas but different beach (Río Sieira)",
    2395: "near Queiruga but different beach (Seiras)",
    2326: "near Tanxil but different beach (Tronco)",
    1993: "near San Miguel Reinante/Area Longa but different beach (Coto)",
    1995: "near Altar/San Cosme but different beach (San Bartolo)",
    2306: "near Camaxe but different beach (O Bao Sur)",
    2149: "near Area Grande but different beach (Vilariño)",
    2154: "near Vilariño-Arnelas/Hio but different beach (Pinténs)",
    2127: "near Limens but different beach (Santa Marta)",
    2134: "near Barra-Nerga but different beach (Viñó)",
    2114: "near Tirán but different beach (Videira)",
    2070: "near Canido but different beach (O Vao)",
    2071: "near Bao but different beach (Fontaíña)",
    2297: "near Compostela but different beach (A Concha)",
    2285: "near O Terrón but different beach (Con da Mina)",
}

HEADER_RE = re.compile(
    r'(\d+)\.\s*(?:Ayuntamiento|Concello)\s*[""\u201c\u201d]([^""\u201c\u201d]+)[""\u201c\u201d]'
)
BEACH_RE = re.compile(r"^(\d{4})\s+(.+)$")
PROV_RE = re.compile(r"^PROVINCIA\s+(.+)$", re.I)
FOOTER_NOISE = ("METEOGALICIA", "Consellería", "meteogalicia.gal", "T. 881")


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "x"


def normalize_name(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    for w in ("praia", "playa", "de", "do", "da", "norte", "sur", "grande", "chica", "pequena"):
        text = re.sub(rf"\b{w}\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def parse_dms(s: str) -> float:
    s = s.strip().replace("º", "°").replace("ª", "")
    m = re.match(
        r"(-?\d+)\s*°\s*(\d+)\s*['\u2019]\s*(\d+(?:\.\d+)?)\s*[\"\u201d]?\s*([NSEWOo])?",
        s,
    )
    if not m:
        raise ValueError(f"bad DMS: {s!r}")
    deg, minute, sec, hemi = m.groups()
    val = abs(int(deg)) + int(minute) / 60 + float(sec) / 3600
    if deg.startswith("-") or (hemi and hemi.upper() in ("S", "W", "O")):
        val = -val
    return val


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists() or dest.stat().st_size == 0:
        urllib.request.urlretrieve(url, dest)
    return dest


def extract_pdf_text(pdf_path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def parse_annex(text: str) -> list[dict]:
    idx = text.find("ANEXO I")
    if idx < 0:
        raise SystemExit("ANEXO I not found")
    text = text[idx:]
    # Drop later annexes
    for stop in ("ANEXO II", "ANEXO III"):
        j = text.find(stop)
        if j > 0:
            text = text[:j]

    beaches: list[dict] = []
    seen_ids: set[int] = set()
    province = None
    concello = None
    header_nums: list[int] = []
    header_names: list[str] = []
    notes: list[str] = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line or any(n in line for n in FOOTER_NOISE):
            continue
        pm = PROV_RE.match(line)
        if pm:
            # new province: validate previous numbering continuous + alpha
            if header_nums:
                expected = list(range(1, len(header_nums) + 1))
                if header_nums != expected:
                    raise SystemExit(f"header numbering gap in {province}: {header_nums}")
                # alphabetical within province (casefold)
                names_cf = [n.casefold() for n in header_names]
                if names_cf != sorted(names_cf):
                    # known Pontevedra disorder around A Guarda / Vilaboa — allow with note
                    notes.append(
                        f"alphabetical violation in {province}: headers={header_names}"
                    )
            province = pm.group(1).strip()
            header_nums = []
            header_names = []
            concello = None
            continue

        hm = HEADER_RE.match(line)
        if hm:
            num = int(hm.group(1))
            name = hm.group(2).strip()
            header_nums.append(num)
            header_names.append(name)
            concello = name
            continue

        bm = BEACH_RE.match(line)
        if bm and concello:
            bid = int(bm.group(1))
            bname = bm.group(2).strip()
            if bid in seen_ids:
                notes.append(f"duplicate idPraia {bid} ({bname}) — keeping first")
                continue
            seen_ids.add(bid)
            c = FORCE_CONCELLO.get(bid, concello)
            beaches.append(
                {
                    "id": bid,
                    "name": bname,
                    "concello": c,
                    "province": province or "",
                }
            )

    if header_nums:
        expected = list(range(1, len(header_nums) + 1))
        if header_nums != expected:
            raise SystemExit(f"header numbering gap in {province}: {header_nums}")

    return beaches, notes


def fetch_mg_coords(beaches: list[dict]) -> None:
    coord_cache = CACHE / "mg_coords.json"
    cached: dict[str, dict] = {}
    if coord_cache.exists():
        cached = json.loads(coord_cache.read_text())

    for i, b in enumerate(beaches):
        key = str(b["id"])
        if key in cached and "lat" in cached[key]:
            b["lat"] = cached[key]["lat"]
            b["lon"] = cached[key]["lon"]
            continue
        url = MG_URL.format(b["id"])
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                pred = data["predPraia"]
                b["lat"] = float(pred["lat"])
                b["lon"] = float(pred["lon"])
                cached[key] = {"lat": b["lat"], "lon": b["lon"]}
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 2:
                    raise SystemExit(f"MG coords fail id={b['id']}: {exc}") from exc
                time.sleep(1.0 + attempt)
        time.sleep(0.3)
        if (i + 1) % 50 == 0:
            coord_cache.write_text(json.dumps(cached))
            print(f"coords {i + 1}/{len(beaches)}", flush=True)

    coord_cache.write_text(json.dumps(cached))


def check_concello_distance(beaches: list[dict], notes: list[str]) -> None:
    by_c: dict[str, list[dict]] = defaultdict(list)
    for b in beaches:
        by_c[b["concello"]].append(b)
    for concello, items in by_c.items():
        mlats = sorted(x["lat"] for x in items)
        mlons = sorted(x["lon"] for x in items)
        mlat = mlats[len(mlats) // 2]
        mlon = mlons[len(mlons) // 2]
        for b in items:
            d = haversine_m(mlat, mlon, b["lat"], b["lon"])
            if d > 20_000:
                notes.append(
                    f"far from concello median: {b['id']} {b['name']} "
                    f"in {concello} d={d/1000:.1f}km"
                )


def load_aemet_galicia(csv_path: Path) -> list[dict]:
    raw = csv_path.read_bytes().decode("iso-8859-1")
    lines = raw.splitlines()
    header = lines[0].split(";")
    out = []
    for line in lines[1:]:
        if not line.strip():
            continue
        cols = line.split(";")
        row = dict(zip(header, cols))
        if row.get("ID_PROVINCIA") not in GALICIA_PROV:
            continue
        lat = parse_dms(row["LATITUD"])
        lon = parse_dms(row["LONGITUD"])
        out.append(
            {
                "id": row["ID_PLAYA"].strip(),
                "name": row["NOMBRE_PLAYA"].strip(),
                "lat": lat,
                "lon": lon,
            }
        )
    return out


def names_agree(mg_name: str, aemet_name: str) -> bool:
    mg_n = normalize_name(mg_name)
    parts = [p.strip() for p in aemet_name.split(",")]
    for part in parts:
        an = normalize_name(part)
        if not an or not mg_n:
            continue
        if an in mg_n or mg_n in an:
            return True
        if SequenceMatcher(None, mg_n, an).ratio() >= 0.7:
            return True
    return False


def match_aemet(beaches: list[dict], aemet: list[dict], notes: list[str]) -> None:
    # nearest AEMET for each MG and nearest MG for each AEMET
    mg_best: dict[int, tuple[float, str]] = {}
    ae_best: dict[str, tuple[float, int]] = {}

    for b in beaches:
        best_d, best_id = 1e18, None
        for a in aemet:
            d = haversine_m(b["lat"], b["lon"], a["lat"], a["lon"])
            if d < best_d:
                best_d, best_id = d, a["id"]
        mg_best[b["id"]] = (best_d, best_id)  # type: ignore[assignment]

    for a in aemet:
        best_d, best_id = 1e18, None
        for b in beaches:
            d = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
            if d < best_d:
                best_d, best_id = d, b["id"]
        ae_best[a["id"]] = (best_d, best_id)  # type: ignore[assignment]

    aemet_by_id = {a["id"]: a for a in aemet}
    pairs = 0
    rejected_name: list[str] = []
    forced: list[str] = []

    for b in beaches:
        d, aid = mg_best[b["id"]]
        if aid is None:
            b["aemetId"] = None
            continue
        # mutual nearest
        if ae_best[aid][1] != b["id"]:
            b["aemetId"] = None
            continue
        if d > 400:
            b["aemetId"] = None
            continue
        a = aemet_by_id[aid]
        if names_agree(b["name"], a["name"]):
            b["aemetId"] = aid
            pairs += 1
            continue
        force_id = AEMET_FORCE_PAIRS.get(b["id"])
        if force_id and force_id == aid:
            b["aemetId"] = force_id
            pairs += 1
            forced.append(f"{b['id']} {b['name']} ↔ {aid} {a['name']} ({d:.0f}m)")
            continue
        reason = AEMET_REJECT_REASONS.get(
            b["id"], "name mismatch with mutual-nearest neighbour"
        )
        rejected_name.append(
            f"{b['id']} | reject | {reason} | nearest {aid} {a['name']} ({d:.0f}m)"
        )
        b["aemetId"] = None

    # FORCE_PAIRS that were not mutual-nearest (rare) — still apply if id exists
    for bid, aid in AEMET_FORCE_PAIRS.items():
        beach = next((x for x in beaches if x["id"] == bid), None)
        if not beach or beach.get("aemetId"):
            continue
        if aid not in aemet_by_id:
            notes.append(f"AEMET_FORCE_PAIRS missing id {aid} for beach {bid}")
            continue
        beach["aemetId"] = aid
        pairs += 1
        forced.append(f"{bid} {beach['name']} ↔ {aid} (forced)")

    notes.append(f"AEMET match pairs: {pairs}")
    if forced:
        notes.append(f"AEMET force pairs ({len(forced)}):")
        notes.extend(f"  - {x}" for x in forced)
    notes.append(f"AEMET rejected by name ({len(rejected_name)}):")
    notes.extend(f"  - {x}" for x in rejected_name)


def apply_dogs_allow(beaches: list[dict], notes: list[str]) -> None:
    by_id = {b["id"]: b for b in beaches}
    marked: list[str] = []
    for bid in sorted(DOGS_ALLOW):
        b = by_id.get(bid)
        if not b:
            notes.append(f"DOGS_ALLOW missing beach {bid}")
            continue
        b["dogs"] = True
        marked.append(f"{bid} {b['name']}, {b['concello']}")
    notes.append(f"DOGS_ALLOW marked ({len(marked)}):")
    notes.extend(f"  - {x}" for x in marked)
    notes.append("DOGS_ALLOW unmatched blog names:")
    notes.extend(f"  - {x}" for x in DOGS_UNMATCHED)


def apply_coord_overrides(beaches: list[dict], notes: list[str]) -> None:
    by_id = {b["id"]: b for b in beaches}
    for bid, (lat, lon) in COORD_OVERRIDES.items():
        b = by_id.get(bid)
        if not b:
            notes.append(f"COORD_OVERRIDES missing beach {bid}")
            continue
        old_lat, old_lon = b.get("lat"), b.get("lon")
        if old_lat == lat and old_lon == lon:
            continue
        b["lat"] = lat
        b["lon"] = lon
        notes.append(f"{bid} | coord {old_lat},{old_lon} → {lat},{lon}")


def apply_name_overrides(beaches: list[dict], notes: list[str]) -> None:
    by_id = {b["id"]: b for b in beaches}
    for bid, new_name in NAME_OVERRIDES.items():
        b = by_id.get(bid)
        if not b:
            notes.append(f"NAME_OVERRIDES missing beach {bid}")
            continue
        old = b["name"]
        if old == new_name:
            continue
        b["name"] = new_name
        notes.append(f"{bid} | {old} | {new_name} | osm")


def unique_slugs(beaches: list[dict]) -> None:
    used: set[str] = set()
    for b in beaches:
        base = slugify(b["name"])
        slug = base
        n = 2
        while slug in used:
            slug = f"{base}-{n}"
            n += 1
        used.add(slug)
        b["slug"] = slug
        b["concelloSlug"] = slugify(b["concello"])


def main() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    pdf = download(PDF_URL, CACHE / "JSON_Pred_Praia_es.pdf")
    csv_path = download(CSV_URL, CACHE / "Playas_codigos.csv")

    text = extract_pdf_text(pdf)
    beaches, notes = parse_annex(text)
    print(f"annex beaches: {len(beaches)}", flush=True)
    if len(beaches) < 600:
        raise SystemExit(f"expected ~615 beaches, got {len(beaches)}")

    # validate header continuity already done in parse; force fixes noted
    notes.append("FORCE_CONCELLO applied: 2095,2096,2099 → Vilaboa")

    print("fetching MG coordinates…", flush=True)
    fetch_mg_coords(beaches)
    apply_coord_overrides(beaches, notes)
    check_concello_distance(beaches, notes)

    aemet = load_aemet_galicia(csv_path)
    notes.append(f"AEMET Galicia beaches: {len(aemet)}")
    match_aemet(beaches, aemet, notes)
    apply_name_overrides(beaches, notes)
    unique_slugs(beaches)
    apply_dogs_allow(beaches, notes)

    concellos = sorted({b["concello"] for b in beaches})
    catalog = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "beachCount": len(beaches),
        "concelloCount": len(concellos),
        "aemetPairCount": sum(1 for b in beaches if b.get("aemetId")),
        "beaches": [
            {
                "id": b["id"],
                "slug": b["slug"],
                "name": b["name"],
                "concello": b["concello"],
                "concelloSlug": b["concelloSlug"],
                "lat": b["lat"],
                "lon": b["lon"],
                "aemetId": b.get("aemetId"),
                **({"dogs": True} if b.get("dogs") else {}),
            }
            for b in beaches
        ],
    }
    (ROOT / "catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (ROOT / "NOTES.md").write_text(
        "# Catalog build notes\n\n" + "\n".join(f"- {n}" for n in notes) + "\n",
        encoding="utf-8",
    )
    print(
        f"wrote catalog.json beaches={catalog['beachCount']} "
        f"concellos={catalog['concelloCount']} pairs={catalog['aemetPairCount']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
