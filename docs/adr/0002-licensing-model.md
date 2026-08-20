# ADR-0002 Под какой лицензией публикуем данные и сайт?

- Status: accepted
- Date: 2026-08-20
- Evidence: [DATA-LICENSE.md](../../DATA-LICENSE.md)

| поле | содержание |
| --- | --- |
| вопрос | Под какой лицензией публикуем данные, если позже планируется монетизация? |
| решение | Публикуем сайт и испечённые данные как есть. ShareAlike у MeteoGalicia / MeteoSIX принимаем. Перед закрытием продукта лицензионную модель пересматриваем. Общей лицензии на весь набор нет — атрибуция по источнику, см. [`DATA-LICENSE.md`](../../DATA-LICENSE.md). |
| причина | MeteoGalicia (`jsonPredPraia`) и MeteoSIX — CC BY-SA 4.0 ([портал Abertos, набор 0111](https://abertos.xunta.gal/es/catalogo/medio-abiente/-/dataset/0111/prediccion-meteorologica-para-las-playas), [набор 0274](https://abertos.xunta.gal/catalogo/medio-abiente/-/dataset/0274/predicion-meteoroloxica-oceanografica)). Copernicus — атрибуция по [CMEMS licence](https://marine.copernicus.eu/user-corner/service-commitments-and-licence), не ShareAlike. AEMET — реиспользование с атрибуцией по [Nota legal](https://www.aemet.es/es/nota_legal). Смешивать их в одну лицензию нельзя. |
| ссылка | [DATA-LICENSE.md](../../DATA-LICENSE.md) |

## Rejected

- Отложить публикацию до выбора коммерческой лицензии — владелец принял публичный сайт сейчас, пересмотр перед закрытием продукта.
- Выдать весь `data/` как CC BY-SA 4.0 — Copernicus и AEMET под этой лицензией не стоят ([DATA-LICENSE.md](../../DATA-LICENSE.md)).

## Reopen

- agent: новая улика со страницы условий источника, которая запрещает текущую публикацию или ShareAlike на производный набор
- owner: `supersede ADR-0002` после показа конфликта в этом сеансе
