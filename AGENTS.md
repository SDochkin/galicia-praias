# Repository guidelines

Static PoC: sea water temperature for Galician beaches (Copernicus IBI + MeteoGalicia / AEMET). Served as GitHub Pages. Live: https://sdochkin.github.io/galicia-praias/

Agent docs are English. ADR are Russian. UI copy is Spanish. Replies to the owner are Russian.

## Layout

- `index.html` — static UI
- `catalog.json` — beach catalog (generated)
- `data/` — baked temperatures (`index.json` + per-concello JSON)
- `scripts/update_beaches.py` — daily bake
- `scripts/build_catalog.py` — one-shot catalog rebuild
- `scripts/diag_wetcell.py` — Mar QA diagnostic (does not change bake)
- `docs/` — research notes and ADR
- `.github/workflows/update-beaches.yml` — scheduled bake + commit of `data/`
- `.cursor/mcp.json` — MCP config

## Decision zones (read before propose/patch)

Before editing symbols listed in [`docs/adr/README.md`](docs/adr/README.md) (gate trigger: `CMEMS_DATASET`, `CMEMS_SEARCH_RADIUS`, `nearest_wet_cell`, `fetch_copernicus_grid`, `extract_copernicus_for_beach`, or adding marine MCP), read the ADR index. Outside that trigger, no ADR applies.

ADR conflict → stop, quote the ADR, do not patch. Code only after explicit `supersede ADR-N` in this session.

## Docs index

| Doc | When |
| --- | --- |
| [`README.md`](README.md) | Secrets, catalog rebuild, bake commands, cron |
| [`docs/adr/README.md`](docs/adr/README.md) | Decisions; gate trigger; when to write ADR |
| [`docs/mar-qa.md`](docs/mar-qa.md) | Evidence for ADR-0001 |
| [`docs/meteosix-api.md`](docs/meteosix-api.md) | MeteoSIX v5 extract (URL, limits, variables, model run times) |
| [`docs/ui-chrome.md`](docs/ui-chrome.md) | when adding a hero figure or editing chrome in `index.html` |
| [`DATA-LICENSE.md`](DATA-LICENSE.md) | Per-source data licences |
| [`NOTES.md`](NOTES.md) | Generated catalog notes |

## Generated files

Do not hand-edit `catalog.json`, `NOTES.md`, or `data/*.json` (exception: owner explicitly asks for diagnostics).

`data/` is committed by the workflow (`git add -A data`); commits under `data/` from Actions are expected.

Catalog name / AEMET / concello / coord overrides live only in `scripts/build_catalog.py` (`NAME_OVERRIDES`, `AEMET_FORCE_PAIRS`, `AEMET_REJECT_REASONS`, `FORCE_CONCELLO`, `COORD_OVERRIDES`).

## Analytics

Do not rename events passed to `track` in [`index.html`](index.html) (comment on `track`). Those names are accumulated Umami series. New field values inside an event are safe. Do not copy the name list here.

## Definition of Done (bake)

After changing bake logic:

```bash
python3 scripts/update_beaches.py --selfcheck
```

Other bake / refresh commands: see [`README.md`](README.md).

## MCP

ERDDAP is configured in [`.cursor/mcp.json`](.cursor/mcp.json). Do not pin package versions in docs. Copernicus credentials exist only as GitHub Secrets (no local login assumed).

## Doc verification checklist

When writing or editing ADR / agent docs:

- Every claim cites a path and section; no citation → do not write the line
- Verdict, not generalization («do not change without X»; never «X does not exist / is worse»)
- Do not copy numbers or constant values; link the symbol or evidence section
- Check relative links from the file’s directory

On ADR, plans, and reports:

- A claim about code behaviour cites file and lines, read in this session. Memory of a previous read does not count
- A claim about an external API is confirmed by a live request or the manual page; the response or quote is saved as evidence
- When citing evidence, cite its units. Do not borrow units from a neighbouring row of the same section
- Do not copy one variable’s method onto another, and do not confuse display with selection. `PRIMARY_ORDER` / `pick_primary` select only daily `beach.t`. Waves and wind are not in that tuple. mar-qa §Шаг 2 is TEMP / `IR_TS_MO_*`; WAV needs other variables and a check that the platform has them

## Skills (when to read)

- `copernicus-product-discovery` — allowed for product search; applying a product change requires `supersede ADR-0001`
- `accessibility-a11y` — when editing combobox / accordion / hints in `index.html`
- `web-design-guidelines` — only if the owner asks for a UI audit
