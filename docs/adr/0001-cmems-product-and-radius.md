# ADR-0001 Менять ли продукт или радиус CMEMS ради температуры у берега?

- Status: accepted
- Date: 2026-08-19
- Evidence: [docs/mar-qa.md](../mar-qa.md)

| поле | содержание |
| --- | --- |
| вопрос | Менять ли продукт или радиус CMEMS ради температуры у берега? |
| решение | Оставить `CMEMS_DATASET` и `CMEMS_SEARCH_RADIUS` как в [`scripts/update_beaches.py`](../../scripts/update_beaches.py). Смена продукта, сужение радиуса и offset для Mar — только после сравнения кандидата с прибрежным буем. |
| причина | Продукт мельче 0.027° существует ([mar-qa §Шаг 1. Разрешение CMEMS](../mar-qa.md#шаг-1-разрешение-cmems)); отказ — из-за отсутствия сравнения с буем, не из-за «точнее не существует». |
| ссылка | [docs/mar-qa.md](../mar-qa.md) |

## Rejected

- ODYSSEA (L3S / L4) без сравнения с прибрежным буем — [mar-qa §Шаг 1. Разрешение CMEMS](../mar-qa.md#шаг-1-разрешение-cmems)
- offset для Mar — [mar-qa §Шаг 2. Буи EMODnet](../mar-qa.md#шаг-2-буи-emodnet) (нет стабильного сдвига)
- сужение до `radius=1` — [mar-qa §Шаг 0. Wet cell](../mar-qa.md#шаг-0-wet-cell) (потеря покрытия)
- установка `copernicus-mcp` — [mar-qa §Шаг 1. Разрешение CMEMS](../mar-qa.md#шаг-1-разрешение-cmems) (нет локального login; креды только в GitHub Secrets)

## Reopen

- agent: сравнение кандидата-продукта с прибрежным буем на ряде суток
- owner: `supersede ADR-0001` после показа конфликта в этом сеансе
