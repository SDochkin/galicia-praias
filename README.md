# Galicia praias — temperatura del agua (PoC)

Sitio estático: Copernicus IBI (+ MeteoGalicia / AEMET).

- Página: https://sdochkin.github.io/galicia-praias/
- Repo: https://github.com/SDochkin/galicia-praias

## Owner checklist

1. GitHub Actions secrets:
   - `AEMET_API_KEY` (clave OpenData)
   - `COPERNICUSMARINE_SERVICE_USERNAME`
   - `COPERNICUSMARINE_SERVICE_PASSWORD`
2. Cuenta Copernicus: https://data.marine.copernicus.eu — Register, confirmar email, aceptar licence. Blue markets: Education & Public Health & Recreation.
3. Tras Pages: Umami website → Website ID en `index.html`.

## Rebuild catalog (raro)

```bash
python3 scripts/build_catalog.py
```

Overrides de nombres / AEMET / concello van en `scripts/build_catalog.py` (`NAME_OVERRIDES`, `AEMET_FORCE_PAIRS`, `AEMET_REJECT_REASONS`, `FORCE_CONCELLO`). No editar `catalog.json` / `NOTES.md` a mano.

## Manual data refresh

```bash
pip install copernicusmarine
export COPERNICUSMARINE_SERVICE_USERNAME=…
export COPERNICUSMARINE_SERVICE_PASSWORD=…
AEMET_API_KEY=… python3 scripts/update_beaches.py
```

Checks:

```bash
python3 scripts/update_beaches.py --selfcheck
python3 scripts/update_beaches.py --limit 5
```

Cron: `.github/workflows/update-beaches.yml` a las **15:00 UTC** (IBI publica ~14:00 UTC).

Datos servidos: `data/index.json` + `data/<concelloSlug>.json`.
