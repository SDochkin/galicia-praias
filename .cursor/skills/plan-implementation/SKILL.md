---
name: plan-implementation
description: >-
  Structured planning for galicia-praias. Use when the user asks for a plan, approach, architecture discussion, or implementation plan. Owner and agent share one plan: short complete sentences, no telegram, no essay. Goal, Scope, Changes, blast radius (JSON fields, UI, cron, ADR gate), Done. Do not copy Linear or test-canon from other repos. Read docs/adr/README.md before planning CMEMS or bake changes.
---

# Plan Implementation

План читают владелец (утверждает) и агент (исполняет). Один текст.

Писать по-русски. Короткие полные предложения: есть подлежащее и сказуемое. Не эссе и не телеграф.

## Before writing

1. Прочитать [`docs/adr/README.md`](../../../docs/adr/README.md).
   Если правка в зоне Gate trigger или конфликтует с `do-not` —
   план = показать ADR (id, do-not, цена, Reopen), **без шагов кода**,
   пока владелец не сказал `supersede ADR-N` в этом сеансе.
2. Найти существующий код; указать paths, которые reuse.
3. Явно: in scope / out of scope.
4. `catalog.json` и `NOTES.md` руками не править — только через
   `scripts/build_catalog.py` (`NAME_OVERRIDES`, `AEMET_FORCE_PAIRS`,
   `AEMET_REJECT_REASONS`, `FORCE_CONCELLO`).
5. План правок `index.html` или UI не переименовывает события `track` (комментарий у `track` в [`index.html`](../../../index.html)). Новые значения полей внутри события можно. `PRIMARY_ORDER` и `pick_primary` выбирают только суточное `beach.t`; волны, ветер и `score` в этот кортеж не входят — [AGENTS.md](../../../AGENTS.md) (On ADR, plans, and reports) и [docs/adr/README.md](../../../docs/adr/README.md) п.9.

Неясный запрос (нет дефекта / цели / ограничения) — спросить, не угадывать.

## How to write

Читатели: владелец и агент. «Concise» в Cursor не разрешает телеграф.

Каждая строка Goal, Changes и overview: подлежащее и сказуемое. Одна мысль — одна строка. Независимая мысль — то, что можно отдельно проверить в Done.

Запрещено в Goal и Changes (тот же критерий, что у `/analyse-plan`):
- ярлык вместо фразы (`Лицо:`, `Оборот:`)
- несколько независимых требований через `;` или «потом»
- фраза без глагола

Разрешено:
- маркированный список
- путь и символ в той же фразе: «На обороте таблицы сразу, без `<details>` (`cardBackHtml`).»

**Goal** — одно полное предложение исхода для пользователя. Не склад фич. Ограничения — отдельные буллеты в Changes, не второе предложение Goal.

**Changes** — что сделать и где (`path`, символ). Несколько действий в одном месте — вложенный список; каждый пункт — предложение.

Не писать «почему» (аудит, эвристики, история бага), пока владелец не попросил. Не дублировать Goal в overview, Подходе и Changes.

`/analyse-plan` помечает телеграф только в Goal и Changes. YAML todos этим правилом не покрыты.

Пример. Было:

> Клик не меняет высоту ряда. Лицо: °C, потом индекс с видимой меткой и тултипом. Оборот: шкалы хорошо/плохо без цифр; таблицы истории сразу, скролл без полосы.

Стало:

- Клик по карточке переворачивает её. Высота ряда не меняется.
- На лице слева температура в °C. Справа индекс с видимой подписью и тултипом (тач, не только hover).
- На обороте части индекса — шкалы «хорошо / плохо» без цифр. Таблицы истории видны сразу. Скролл внутри карточки, полоса скрыта.

## Plan shape (только нужные секции)

1. **Goal** — одно полное предложение: что станет правдой для пользователя. Не склад фич. Ограничения — отдельные буллеты в Changes.
2. **Scope** — делаем / не делаем.
3. **Подход** — один; варианты только если без выбора нельзя продолжить.
4. **Changes** — path + символ + что меняется (reuse vs new).
5. **Steps** — нумерация только если важен порядок.
6. **Blast radius** — правило ниже.
7. **Done** — как проверить конкретно.
8. **Open questions** — только блокеры; иначе секцию не писать.

Утверждения «существует / вызывается / меняем X» — только после grep/чтения
кода (path, символ). Иначе не писать или пометить как assumption.
Goal и Scope от пользователя проверять кодом не нужно.

## Blast radius

Найти **реальных потребителей** места правки. Для каждого: guard in scope
или явный out-of-scope риск. Не выдумывать empty/loading/validation,
если это не следует из потребителя.

Цепочка репо (идти по ней, не по каталогу гипотез):

| Меняем | Потребители |
| --- | --- |
| `scripts/build_catalog.py` / поля каталога | `catalog.json` → `scripts/update_beaches.py` |
| `scripts/update_beaches.py` (логика/выход) | `write_data_split`, `run_selfcheck`, `data/*.json`, `index.html`, `.github/workflows/update-beaches.yml` |
| форма `data/index.json` / `data/<slug>.json` | `index.html`: `fetchedAt`, `concellos`, `beachConcello`, `top`; у пляжа `t`, `source`, `trend`, `sources` |
| только `index.html` | depth-1 callers в `index.html`, включая другие функции того же файла; поля data не выдумывать |
| workflow / флаги bake | README cron, `--skip-copernicus`, secrets |
| символы Gate trigger в ADR | не кодить — см. Before writing |

Локальная правка без других потребителей — одна строка (`low risk: …`).
Много потребителей — группировать по флоу, сценарий не выкидывать.

При записи заголовка: для каждого символа, который план называет, и для каждого
экспорта файла правки — grep вызывающих и импортёров, depth 1, только
зависимость от контракта (сигнатура, семантика, форма выхода, формат хранения).
Вызывающий в том же файле — отдельный символ в строке `path:`. Строка
`index.html: sortBeaches` не закрывает `nearbyBeaches`. Вызывающих у
caller-only строк не грепать.

## Done

Без чеклиста «на всякий случай». Только то, что закрывает Goal.

- Логика bake (`pick_primary`, `merge_history`, `attach_primary_fields`, split):
  `python3 scripts/update_beaches.py --selfcheck`.
- Каталог: `python3 scripts/build_catalog.py` — только если rebuild в scope.
- UI: какое поле/состояние в `index.html` должно отличаться.

Новый тестовый раннер не предлагать. Расширять `run_selfcheck` — только если
без этого конкретный сценарий отъедет незаметно; иначе в Scope: не трогаем.

## Self-check

- Исполнитель может сделать работу по paths и steps.
- Нет пропущенного потребителя.
- Нет шагов, без которых Goal всё равно выполняется → убрать или out of scope.
- Один подход.
- ADR не нарушен (или план — показ конфликта, не код).
- Владелец понимает Goal без чата.
- В Goal и Changes нет телеграфа (ярлык вместо фразы, `;` / «потом» между требованиями, нет глагола).
- Нет непрошеного «почему».
- План не переименовывает события `track`.
