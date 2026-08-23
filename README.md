# Galicia praias — temperatura del agua (PoC)

Sitio estático: Copernicus IBI (+ MeteoGalicia / AEMET / MeteoSIX).

- Página: https://sdochkin.github.io/galicia-praias/
- Repo: https://github.com/SDochkin/galicia-praias

## Owner checklist

1. GitHub Actions secrets:
   - `AEMET_API_KEY` (clave OpenData)
   - `COPERNICUSMARINE_SERVICE_USERNAME`
   - `COPERNICUSMARINE_SERVICE_PASSWORD`
   - `METEOSIX_API_KEY` (clave API MeteoSIX)
2. Cuenta Copernicus: https://data.marine.copernicus.eu — Register, confirmar email, aceptar licence. Blue markets: Education & Public Health & Recreation.
3. Tras Pages: Umami website → Website ID en `index.html`.

## Rebuild catalog (raro)

```bash
python3 scripts/build_catalog.py
```

Overrides de nombres / AEMET / concello / coords van en `scripts/build_catalog.py` (`NAME_OVERRIDES`, `AEMET_FORCE_PAIRS`, `AEMET_REJECT_REASONS`, `FORCE_CONCELLO`, `COORD_OVERRIDES`). No editar `catalog.json` / `NOTES.md` a mano.

## Manual data refresh

```bash
pip install copernicusmarine
export COPERNICUSMARINE_SERVICE_USERNAME=…
export COPERNICUSMARINE_SERVICE_PASSWORD=…
export METEOSIX_API_KEY=…
AEMET_API_KEY=… python3 scripts/update_beaches.py
```

Checks:

```bash
python3 scripts/update_beaches.py --selfcheck
python3 scripts/update_beaches.py --limit 5
python3 scripts/update_beaches.py --coverage   # MeteoSIX only; does not write data/
```

Cron (`.github/workflows/update-beaches.yml`), one fetch+commit pair per source:

- **07:00 UTC** — layers (`--skip-mg --skip-aemet --skip-meteosix --skip-copernicus`), then MG (`--skip-copernicus --skip-meteosix --skip-aemet`), then AEMET (`--skip-copernicus --skip-meteosix --skip-mg`)
- **10:00 UTC** — MeteoSIX (`--skip-copernicus --skip-mg --skip-aemet`); ROMS ~09:30 UTC
- **15:00 UTC** — Copernicus (`--skip-meteosix --skip-mg --skip-aemet`), then MG, then AEMET; IBI ~14:00 UTC
- **workflow_dispatch** — Copernicus, MeteoSIX, layers, MG, AEMET in that order

Datos servidos: `data/index.json` + `data/<concelloSlug>.json`.

Licencias de las fuentes: [`DATA-LICENSE.md`](DATA-LICENSE.md).
