# Mar/Costa QA — 2026-08-19

Decision: [ADR-0001](adr/0001-cmems-product-and-radius.md)

Приоритет: вода у берега (costa). Вопрос: есть ли данные точнее MG-прогноза + IBI 0.027° (~3 км)?

**Вердикт: оставить как есть.** Bake и UI не менять. `scripts/update_beaches.py` в этом прогоне не трогаем (временный хук диагностики откатан).

## Прогон

- Дата: 2026-08-19
- `dataset_id`: `cmems_mod_ibi_phy_anfc_0.027deg-2D_PT1H-m`, переменная `thetao`
- Окно истории bake: 7 суток (`HISTORY_DAYS`)
- Скрипт шага 0: `scripts/diag_wetcell.py` (Actions run `32245741618`, сетка скачалась; push data упал на грязном дереве из-за удаления sentinel — на `data/` не влияет)

## Шаг 0. Wet cell

615 пляжей, Mar при `radius=2`: **543** (72 без Mar, 11.7%).

Гистограмма `d2 = di²+dj²` выбранной ячейки:

| d2 | пляжей | доля от 543 |
| --- | --- | --- |
| 0 (своя клетка) | 9 | 1.7% |
| 1 | 236 | 43.5% |
| 2 | 117 | 21.5% |
| 3 | 0 | 0% |
| ≥4 | 181 | 33.3% |

- медиана `d2` = **2** (не 0)
- доля, которой нужно кольцо ±2 (`d2>1`): **54.9%**
- потеря покрытия: `radius=1` → **41.1%**; `radius=0` → **98.5%**

Топ дальности (рии, как ожидалось): Poio (`praia-da-canteira` 9.4 км), Camariñas, Sada, Carnota, Ferrol, Cambados, O Vicedo, A Coruña/Oza.

Гейт «ячейка не ускакивает» **закрыт**: медиана не 0, radius=2 срабатывает у большинства. Сужать `CMEMS_SEARCH_RADIUS` нельзя — потеря ≫ 2%.

## Шаг 1. Разрешение CMEMS

Скилл `copernicus-product-discovery` (репозиторий metadata, лексический отбор `--no-rerank` / таблица `outputs/csv`; `npx skills add` на Node 18 не встал). Запрос: daily SST Iberia Biscay Galicia coastal NRT.

Кандидаты мельче 0.027°, NRT, Иберия:

- `SST_ATL_PHY_L3S_NRT_010_037` ODYSSEA L3S — **0.02°** (дыры по облакам)
- `SST_ATL_SST_L4_NRT_OBSERVATIONS_010_025` ODYSSEA L4 — **0.02°** gap-free satellite foundation SST

Гейт по разрешению **открыт**. Это не зона купания: ~2.2 км против ~3 км у IBI, маска суши того же класса. `copernicus-mcp` **не ставили**: локального `copernicusmarine login` нет (креды только в GitHub Secrets), `status` без AuthError недоступен. Сравнение продуктов с буем не делали → ветка «сменить продукт» не выбирается.

Грубее IBI (отсечены): OSTIA/L4 0.05°, глобальная физика, reanalysis.

## MCP

`.cursor/mcp.json`: только `erddap` через `wsl` + `uvx --from erddap-mcp --with 'mcp[cli]>=1.0.0,<2'` (голый `uvx erddap-mcp` падает: mcp 2 без `FastMCP`). Инструменты сервера: `erddap_search_datasets`, `erddap_get_all_datasets`, `erddap_get_tabledap_data`, `erddap_list_servers`. EMODnet: `server_url=https://erddap.emodnet-physics.eu/erddap`.

## Шаг 2. Буи EMODnet

Поиск `sea_water_temperature` по bbox Галисии на EMODnet tabledap даёт транзитные круизы, не станции. Рабочий путь: `EP_PLATFORMS_METADATA_V2` (успешный tabledap, полный csv, фильтр bbox локально).

Пригодные мооринги (≤15 км от тестовой точки, TEMP, последнее наблюдение ≤7 суток):

| код | имя | тип | км | тестовая точка | берег / море |
| --- | --- | --- | --- | --- | --- |
| 6201031 | A Guarda buoy | Mooring | 4.0 | `praia-de-codesal` | прибрежный |
| 6201070 | Langosteira II coastal buoy | Mooring | 8.6 | `praia-de-bens` | прибрежный (внешний порт A Coruña, не Fisterra) |
| 6201039 | Rande pillar | Mooring | 13.3 | `praia-dos-placeres` | риа (не открытое море) |

`praia-de-langosteira` (Fisterra): годного буя ≤15 км нет.

Значения TEMP: EMODnet files (`er3webapps`, `EP_PLATFORMS_FILES`) отвечали 503; агрегатного TEMP tabledap нет. Ряд взят из официального репозитория тех же платформ (integrator INSTAC): публичный S3 `mdl-native-03` / `INSITU_IBI_PHYBGCWAV_DISCRETE_MYNRT_013_033`, файлы `IR_TS_MO_<code>_<YYYYMMDD>.nc`, ближайший к полудню час (или единственный суточный слот).

Таблица (costa = MG `t` на дату, если есть в `data/*.json` на 2026-08-18 bake; mar = Copernicus; буй = INSTAC TEMP на первой мокрой глубине):

| дата | точка | costa | mar | буй | Δ(mar−буй) | Δ(costa−mar) |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-08-19 | codesal / A Guarda 4 км | 19.0 | 15.9 | 16.49 | −0.59 | 3.1 (?) |
| 2026-08-18 | codesal / A Guarda 4 км | 19.0 | 15.1 | 15.52 | −0.42 | 3.9 (?) |
| 2026-08-17 | codesal / A Guarda 4 км | — | 14.9 | 15.82 | −0.92 | — |
| 2026-08-16 | codesal / A Guarda 4 км | — | 14.8 | 14.99 | −0.19 | — |
| 2026-08-15 | codesal / A Guarda 4 км | — | 14.8 | 14.92 | −0.12 | — |
| 2026-08-14 | codesal / A Guarda 4 км | — | 17.9 | 18.19 | −0.29 | — |
| 2026-08-13 | codesal / A Guarda 4 км | — | 18.1 | 18.44 | −0.34 | — |
| 2026-08-12 | codesal / A Guarda 4 км | — | 18.2 | 18.07 | +0.13 | — |
| 2026-08-19 | bens / Langosteira II 8.6 км | 15.0 | 16.5 | 15.90 | +0.60 | −1.5 |
| 2026-08-18 | bens / Langosteira II 8.6 км | 15.0 | 16.2 | 14.40 | +1.80 | −1.2 |
| 2026-08-17 | bens / Langosteira II 8.6 км | — | 16.7 | 13.60 | +3.10 | — |
| 2026-08-16 | bens / Langosteira II 8.6 км | — | 17.3 | 15.70 | +1.60 | — |
| 2026-08-15 | bens / Langosteira II 8.6 км | — | 17.9 | 17.00 | +0.90 | — |
| 2026-08-14 | bens / Langosteira II 8.6 км | — | 18.5 | 19.30 | −0.80 | — |
| 2026-08-13 | bens / Langosteira II 8.6 км | — | 18.8 | 19.70 | −0.90 | — |
| 2026-08-12 | bens / Langosteira II 8.6 км | — | 18.8 | 20.70 | −1.90 | — |
| 2026-08-19 | placeres / Rande 13.3 км | 18.0 | нет Mar | 18.02 | — | — |
| 2026-08-18 | placeres / Rande 13.3 км | 16.0 | нет Mar | 19.13 | — | — |

MG `history` после UI-плана копится по одному bake: для прошлых дат costa часто пуст. Колонка costa — масштаб `|costa−mar|` и «?», не «MG врёт».

Медиана Δ(mar−буй): A Guarda **−0.29 °C** (n=8); Langosteira II **+0.90 °C** (знак плавает). Стабильного сдвига ≥1 °C на ≥5 сутках нет.

Rande валидирует воду риа, не IBI (у placeres Mar нет: ячейка IBI не находится в радиусе 2).

## Почему не другие ветки

1. «Закрыть план» в формулировке гейта 1 — нет: мельче 0.027° продукт есть.
3. Сменить продукт — нет сравнения ODYSSEA с прибрежным буем.
4. Offset у Mar — нет стабильного ≥1 °C / 5 суток (A Guarda честен).
5. Сузить радиус — потеря 41% при `radius=1`.

Уточнить costa на 615 пляжах нечем: три мооринга не сеть, ODYSSEA не зона купания, IBI у берега прыгает в мокрую клетку, но это цена 3 км, а не повод резать покрытие.

## MeteoSIX покрытие — 2026-08-20

Метод: `python3 scripts/update_beaches.py --coverage` ([scripts/update_beaches.py](../scripts/update_beaches.py)). `data/` не пишет. Ключ: env `METEOSIX_API_KEY`.

Точка пробы: первая запись [catalog.json](../catalog.json) (`praia-de-bens`, lon/lat из каталога). `models=` по очереди (вода ROMS, не MOHID; ветер/воздух/дождь WRF):

1. `ROMS,WRF,WRF,USWAN,USWAN,WRF` (v5 приложение A1: SWAN → USWAN)
2. `ROMS,WRF,WRF,SWAN,SWAN,WRF`
3. `ROMS,WRF,WRF,WW3,WW3,WRF`

Победитель — первая строка, у которой в ответе есть значения волн (иначе первая с любыми часами). Тот же `models=` идёт в ingest 10:00 UTC.

Переменные запроса: `sea_water_temperature`, `temperature`, `wind` (`units` `ms_deg`), `significative_wave_height`, `relative_peak_period`, `precipitation_amount`. Пачки `coords` по 20. Имена `/findPlaces` не матчим.

Схема JSON ответа — [docs/meteosix-api.md](meteosix-api.md) §Ответ JSON (мануал v5 / v4 §6.4). Ключи bake: `features[]` (порядок как `coords`), `exception`, `properties.days[].variables[].values[]` (`value` / `moduleValue`, `timeInstant`).

Доли пляжей по переменным — stdout `--coverage` (`var: N/615 (p%)`). В этом сеансе ключа в env не было; секрет есть в GitHub Actions (`METEOSIX_API_KEY`, 2026-08-20). Прогон с ключом дописывает числа в эту секцию, заголовки §Шаг 0/1/2 не трогать.

