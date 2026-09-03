# UI chrome for figures

Read this file when adding a hero figure or editing chrome in [`index.html`](../index.html).

## Names

- `role` — what the quantity is. Visible label: `indiceLabel` on `card-score-label` under the index in [`index.html`](../index.html); the `mar` label on the details row.
- `nature` — how the number was obtained, when it is not a shoreline measurement. Visible word on the line below the degrees. Copy: i18n key `tempNature` in [`index.html`](../index.html).
- `status` — exception relative to the usual nature. Tooltip only: `disagree` and `nodata` in [`index.html`](../index.html).

## Carrier class

Water degrees outside a table cell, with a unit that looks like a sensor reading. That is the face cluster `card-temp-cluster` and the details row built in `cardBackHtml` for `t("mar")` when `marOf` has a number. A table column header is the cell's `role`. The cell does not need a separate `nature` word.

## Display

A carrier of this class shows a visible `nature` word on the line below the degrees (`card-temp-cluster`, `fact-figure-body` in [`index.html`](../index.html)). The page `subtitle`, the footer, and a lone «?» do not carry this class. «?» only when the word is not there yet: no number, `nodata`.

The hint button is that word, same pattern as `card-score-label`. There is no second glyph beside the word.

A dimensionless index that already has a visible `role` does not get a second `nature` word.

The `nature` word stands whenever the figure is a number. The tooltip names the source (`segun`). One source is still that source: the tooltip says who it is. `data-kind` is `status` when `disagree` or `nodata` applies; otherwise `nature`. Do not rename `hint_open`. Do not delete the `subtitle` key.
