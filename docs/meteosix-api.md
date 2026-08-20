# MeteoSIX API v5 — выписка

Источник: [API_MeteoSIX_v5_gl.pdf](https://meteo-estaticos.xunta.gal/datosred/infoweb/meteo/proxectos/meteosix/API_MeteoSIX_v5_gl.pdf), Setembro de 2025, версия API 5.0.0. Прочитан 2026-08-20. Это не код и не вход в bake.

## Базовый URL

Раздел «Cuestións xerais»:

- домен сервиса: `https://servizos.meteogalicia.es/apiv5/`
- общая структура запросов и все примеры в мануале: `https://servizos.meteogalicia.gal/apiv5/`

Оба написания стоят в мануале рядом. Операции: `GET`/`POST`, путь например `/getNumericForecastInfo`, `/findPlaces`, `/getTidesInfo`, `/getSolarInfo`. Ключ: параметр `API_KEY` обязателен. Ключ v4 для v5 перевыпускать не нужно (тот же раздел, примечание к §3.1).

## Лимиты

Раздел «Parámetros comúns» (§4.2):

- `locationIds`: максимум **20** идентификаторов на запрос; больше — исключение.
- `coords`: максимум **20** пар `lon,lat` (пары через `;`); больше — исключение.
- ровно один из `locationIds` / `coords` должен быть задан.

Раздел «Rango temporal» операции `/getNumericForecastInfo` (§6.2):

- максимум **7** суток на запрос;
- нижняя граница — начало текущих суток;
- шаг данных — **1 час** (раздел «Modelos de predición numérica»).

**Частота запросов (quota / requests per minute).** В мануале v5 такого ограничения нет: ни в «Cuestións xerais», ни в «Operacións», ни в приложении исключений.

## Переменные `/getNumericForecastInfo` (§6.1)

Имена — как в таблице мануала. Нужные нам:

| имя | что | модели | тип | единица по умолчанию |
| --- | --- | --- | --- | --- |
| `sea_water_temperature` | температура воды | ROMS, MOHID | целое | `degC` |
| `wind` | ветер: модуль + направление (`moduleValue`, `directionValue`) | WRF | два вещественных с 2 знаками | `kmh_deg` (есть `ms_deg`) |
| `significative_wave_height` | высота волны | WW3, SWAN | вещественное с 2 знаками | `m` |
| `mean_wave_direction` | направление волны | WW3, SWAN | вещественное с 2 знаками | `deg` |
| `relative_peak_period` | период волны | WW3, SWAN | целое | `s` |
| `temperature` | температура воздуха | WRF | целое | `degC` |
| `precipitation_amount` | осадки за предыдущий час | WRF | вещественное с 2 знаками | `lm2` (л/м²) |

Волновые переменные в v5 **есть**. Приложение A1 при этом говорит, что запросы к модели `SWAN` в v5 нужно менять на `USWAN` / сетку `Galicia`; таблица §6.1 по-прежнему перечисляет SWAN. Что именно отправлять в `models=` — проверять живым запросом, не этой выпиской.

По умолчанию, если `variables` не задан: `sky_state,temperature,wind,precipitation_amount` (вода и волны в этот набор не входят).

`autoAdjustPostion` (орфография мануала) по умолчанию `true`: у берега точка съёма для воздуха, ветра и океанографии может чуть сместиться.

## Время окончания прогонов (UTC, приблизительные)

Раздел «Modelos de predición numérica»: час окончания «может меняться ото дня ко дню»; после конца прогона ещё несколько минут до появления в API.

| модель | сетка | старт | конец примерно | первая час прогноза | горизонт |
| --- | --- | --- | --- | --- | --- |
| WRF | 1 km | 00:00 | 07:30 | 01:00 | 96 h |
| WRF | 4 / 12 / 36 km | 00:00 и 12:00 | 05:00 и 17:00 | 01:00 и 13:00 | 96 h / 84 h |
| WW3 | Galicia 0,05°; Iberica 0,25°; AtlanticoNorte 0,5° | 00:00 и 12:00 | 05:00 и 17:00 | 12:00 и 00:00 | 109 h / 97 h |
| SWAN | Galicia (variable) | 00:00 | 06:30 | 00:00 | 97 h |
| ROMS | Galicia 0,02° | 00:00 | 09:30 | 00:00 | 97 h |
| MOHID | Artabro / Arousa / Vigo 0,003° | 00:00 | 12:30 | 00:00 | 49 h |

Морская температура воды (ROMS) готова около 09:30 UTC; MOHID — около 12:30 UTC.

Письмо выдачи ключа (не квота, не запрет второго запроса тем же утром): «la mayoría de los modelos se ejecutan una única vez al día, generalmente a primera hora de la mañana, por lo que peticiones reiteradas a lo largo del día no aportarían nueva información». Bake поэтому не зовёт MeteoSIX в слотах 07:00 и 15:00 UTC; слот 10:00 UTC — один запрос после ROMS. Покрытие (`--coverage`) и ingest в одно утро — тот же прогон модели.

Bake не использует MOHID (готово ~12:30 UTC, позже слота 10:00). Вода — ROMS.

## Ответ JSON `/getNumericForecastInfo`

Мануал v5, структура как в v4 §4.7 / §6.4 (прочитан 2026-08-20). Ключи, на которые смотрит bake (`scripts/update_beaches.py`, `parse_meteosix_feature`):

- корень: `features[]` — тот же порядок, что пары в `coords`
- `features[i].exception` — точка вне сетки / ошибка; `code`, `message`
- `features[i].geometry.coordinates` — `[lon, lat]` фактической точки (`autoAdjustPostion`)
- `features[i].properties.days[]`
  - `timePeriod.begin.timeInstant` / `end.timeInstant` — `yyyy-MM-ddTHH:mm:ss+XX`
  - `variables` — `null`, если на день нет данных
  - `variables[]`: `name`, `model`, `grid`, `units` (у `wind` — `moduleUnits` / `directionUnits`), `geometry`, `values`
  - `values[]`: `timeInstant`, `modelRun`; скалярные переменные — `value`; `wind` — `moduleValue`, `directionValue`
  - нет данных на час: `value` / `moduleValue` = `null`

Парсер не выдумывает других ключей. Живая проверка `models=` (USWAN / SWAN / WW3) — `python3 scripts/update_beaches.py --coverage`.
