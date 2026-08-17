# Galicia praias — temperatura del agua (PoC)

Sitio estático: pronósticos de MeteoGalicia (+ AEMET si hay secret).

- Página: https://sdochkin.github.io/galicia-praias/
- Repo: https://github.com/SDochkin/galicia-praias

## Owner checklist

1. GitHub Actions secret `AEMET_API_KEY` (clave OpenData nueva).
2. Tras Pages: Umami website → pasar Website ID al agente / pegar en `index.html`.
3. Tráfico local en Galicia; el contador de 30 días empieza con el primer post.

## Rebuild catalog (raro)

```bash
python3 scripts/build_catalog.py
```

## Manual data refresh

```bash
AEMET_API_KEY=… python3 scripts/update_beaches.py
```

Cron: `.github/workflows/update-beaches.yml` a las 06:00 UTC.
