# TODOS

## status.html светофор — honest «источник недоступен» (current-run error)

- **What:** Прокинуть ошибку текущего run (`WatchReport.error`) в рендер светофора, чтобы
  строка/ВУЗ с упавшим в этот час источником показывалась как «источник недоступен, данные
  на <время>», а не как текущий (возможно зелёный) статус из последнего успешного snapshot.
- **Why:** Светофор кадрирует данные как «прямо сейчас». `_gather` (dashboard.py:30) читает
  последний успешный snapshot; ошибка текущего run живёт только в `WatchReport` (pipeline.py),
  до страницы не доходит. При падении сайта ВУЗа пользователь видит вчерашний зелёный как
  сегодняшний. Codex поймал это на eng-review 2026-07-16.
- **v1 mitigation (уже в плане):** бейдж «данные устарели» по `updated_at` + `STALE_HOURS`.
  Покрывает ~80%, но не отличает «упало сейчас» от «просто давно».
- **Where to start:** прокинуть `list[WatchReport]` текущего run в `render_pages`/`build_status_html`
  (сейчас сигнатура видит только store); пометить watch с `report.error` бейджем в светофоре.
- **Depends on / blocked by:** ничего. Меняет сигнатуру `render_pages`.
- **Deferred at:** /plan-eng-review 2026-07-16 (design denis-main-design-20260716-132855.md).
