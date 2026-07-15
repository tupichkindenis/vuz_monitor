# Спек: страница «окружение» в списке МИРЭА (`docs/mirea-list.html`)

**Дата:** 2026-07-15
**Статус:** дизайн утверждён, готов к плану реализации
**Автор:** brainstorming-сессия (Claude + Денис)

## 1. Проблема и цель

Текущее веб-представление (`index.html` карточки, `table.html` десктоп-таблица,
`mirea-scores.html` подгрузка баллов) показывает **только нашу собственную строку** —
место, балл, флаги прохода для отслеживаемого кода `1366129`. Не видно, **кто именно**
стоит рядом: с каким баллом и приоритетом идут конкуренты вокруг нас.

**Цель.** Новая публичная страница `docs/mirea-list.html` («окружение») показывает живое
положение в **одном конкретном конкурсе** МИРЭА: всех абитуриентов на нашем месте и выше
плюс следующих 10 ниже, в раскладке официального списка priem.mirea.ru, с подсветкой нашей
строки. Так видно реальную конкурентную картину вокруг нашей позиции.

**Конкурс из ссылки** (первичная цель): МИРЭА — платно, «1. Интеллектуальные системы
управления и обработки информации»,
`comp_ids=1862638623058304310` → `config.yaml` watch с
`params: { "competitions[]": "1862638623058304310", edu_level: 2 }`.
В нём код `1366129` сейчас №1.

## 2. Что уже есть (не переизобретаем)

- **Полные списки уже сохраняются.** Каждый снапшот в `state.db` (`snapshots.payload`)
  содержит **всех** абитуриентов конкурса как `Entrant` с полями `place`, `code_display`,
  `priority`, `entrance_score` (Сумма за ВИ), `achievement_score` (Балл за ИД),
  `final_score` (Σ баллов), `paid_ok` (условия платного), `consent` (согласие),
  `passing_main`/`passing_real` (флаги ВП), `is_bvi`. Новый сетевой запрос и парсинг **не
  нужны**.
- **Конкурс из ссылки уже мониторится** — присутствует в `config.yaml`, снапшоты копятся
  ежечасно.
- **Многостраничная генерация** — `dashboard.render_pages(config, store)` возвращает
  `{filename: html}` из одного прохода по `state.db`; пайплайн пишет их в `docs/` каждый час
  (`_render_dashboard`). Страница баллов включается только при наличии данных (`has_scores`)
  — тот же приём применяем для новой страницы.
- **Хелперы форматирования** в `format.py`: `esc`, `g` (балл), `yesno`, `pass_real`,
  `is_paid`, `fmt_source_time`. Хелперы времени в `dashboard.py`: `_parse`, `_fetched_msk`.

## 3. Решения (утверждены в брейнсторме)

| Вопрос | Решение |
|---|---|
| Охват | **Только этот конкурс** (флаг у одного watch), но механизм переиспользуемый. |
| Колонки | **Как на офсайте**: № · Код · Приор · Платн · ВИ · ИД · Σбалл · Примечание. Без попредметных «Оценки» и «Преим. право». |
| Размещение | **Новая страница** `docs/mirea-list.html`, ссылка в шапке всех страниц. |
| Коды | **Показывать полностью** (без маски) — для всех строк, включая нашу. Отступление от `mask_code` на других страницах; осознанно, коды публичны на офсайте. |

## 4. Архитектура

Три изолированных блока, каждый тестируется отдельно.

### 4.1 Конфиг: флаг `track_neighbors`

- Добавить поле `track_neighbors: bool = False` в `WatchConfig` (`config.py`).
- Парсить в `load_config`: `track_neighbors=bool(w.get("track_neighbors", False))` — ровно
  по образцу существующего `track_scores`.
- В `config.yaml` (приватный, gitignored) выставить `track_neighbors: true` на платном watch
  «1. Интеллектуальные системы…». В `config.example.yaml` — задокументировать флаг с
  комментарием.

**Интерфейс:** `watch.track_neighbors` → `bool`. Зависимость: только YAML.

### 4.2 Сбор: `_gather_neighbors(config, store) -> list[dict]`

Новая функция в `dashboard.py` (рядом с `_gather_score_progress`). Для каждого watch с
`track_neighbors`:

1. `snap = store.load_prev(watch.watch_id)`; если `None` — пропустить (страница/секция не
   создаётся).
2. `our_codes = { normalize_code(c) for c in config.resolve_codes(watch) }`.
3. `ranked = sorted([e for e in snap.entrants if e.place is not None], key=place)` +
   отдельно `unranked = [e for e in snap.entrants if e.place is None]` (в хвост, обычно
   пусто).
4. Найти нашу позицию: `our = min(place of e for e in ranked if e.code in our_codes)`.
   - **Если найдена:** `rows = [e for e in ranked if e.place <= our_place]` +
     `следующие NEIGHBORS_AFTER (=10)` строк из `ranked` ниже `our_place`.
   - **Если наш код отсутствует в списке** («выбыл»): `rows = ranked[:11]`, флаг
     `we_absent=True`.
5. Вернуть спец:
   ```python
   {
     "title": snap.meta.title or watch.name,
     "updated_at": snap.meta.updated_at,   # источниковое время
     "fetched_at": snap.fetched_at,        # наше время выборки (fallback)
     "paid": is_paid(snap.meta.title) or is_paid(watch.group or watch.name),
     "our_codes": our_codes,               # нормализованные, для подсветки
     "we_absent": bool,
     "rows": [Entrant, ...],               # в порядке места
   }
   ```

**Интерфейс:** `(config, store) -> list[spec]`. Зависимость: `Store.load_prev`,
`Snapshot.entrants`, `config.resolve_codes`, `is_paid`, `normalize_code`. Чистая функция над
состоянием — легко тестируется на собранном вручную `Snapshot`.

**Константа:** `NEIGHBORS_AFTER = 10`.

### 4.3 Рендер: `build_neighbors_html(specs, now=None) -> str`

Новая функция в `dashboard.py`. Одна `<section>` на конкурс:

- Заголовок `<h2>` = `title`; подпись «список по состоянию на &lt;источниковое время или
  fetched_at&gt;».
- Если `we_absent` — плашка «вашего кода нет в этом списке — показан топ-11».
- Таблица со «шапкой» офсайта. Колонки (строго этот порядок):

  | Заголовок | Источник | Формат |
  |---|---|---|
  | № | `e.place` | число |
  | Код | `e.code_display` | **полностью** (без `mask_code`) |
  | Приор | `e.priority` | число, «—» если None |
  | Платн / Согл | `e.paid_ok` если `paid` иначе `e.consent` | `yesno`; заголовок «Платн»/«Согл» по `spec["paid"]` |
  | ВИ | `e.entrance_score` | `g`, «—» если пусто |
  | ИД | `e.achievement_score` | `g`, «—» если пусто |
  | Σбалл | `e.final_score` | `g`, «—» если пусто |
  | Примечание | флаги прохода | см. ниже |

- **Примечание** (переиспользуем цветовую логику карточек):
  - `passing_real == True` → «планируется к зачислению», строка/ячейка зелёная.
  - `passing_real == False and passing_main == True` → «—», строка янтарная (в пределах мест
    по баллам, но впереди согласия).
  - иначе (оба False, либо оба None — источник без флагов) → «—», нейтрально.
- **Наша строка:** `normalize_code(e.code_display) in spec["our_codes"]` → жёлтая подсветка
  (`--you`, как `tr.you` на странице баллов) + маркер «◄ вы» в колонке №. Подсветка «вы»
  имеет приоритет над зелёной/янтарной заливкой.
- Статичная таблица в порядке места (как офсайт). **Без** сортировки/фильтров/JS.
- Горизонтальный скролл-контейнер на узких экранах (как `.cmp-scroll` на странице баллов).

Стиль — отдельная строковая константа `_NEIGHBORS_STYLE` (по образцу `_SCORE_STYLE`):
theme-aware, self-contained, без внешних CSS/JS.

Обёртка страницы (`<!doctype>` … шапка со ссылками … секции … футер) — по образцу
`build_score_progress_html`.

### 4.4 Интеграция в `render_pages` и шапку

- В `render_pages`:
  ```python
  neighbors = _gather_neighbors(config, store)
  has_neighbors = bool(neighbors)
  ...
  pages["index.html"]  = build_html(..., link_scores=has_scores, link_neighbors=has_neighbors)
  pages["table.html"]  = build_table_html(..., link_scores=has_scores, link_neighbors=has_neighbors)
  if has_scores:     pages["mirea-scores.html"] = build_score_progress_html(specs, ...)
  if has_neighbors:  pages["mirea-list.html"]   = build_neighbors_html(neighbors, ...)
  ```
- Новая ссылка `_LINK_LIST = '<a class="page-link" href="mirea-list.html">👥 окружение</a>'`.
- Добавить параметр `link_neighbors: bool = False` в `build_html`, `build_table_html`,
  `build_score_progress_html`; подмешивать `_LINK_LIST` в их summary-бары (по образцу того,
  как сейчас подмешивается `_LINK_SCORES`).
- На самой `mirea-list.html` — обратные ссылки `_LINK_CARDS + _LINK_TABLE (+ _LINK_SCORES при
  наличии)`.
- **Пайплайн (`pipeline.py`) не меняем** — новая страница читает уже сохранённый снапшот; она
  появится в выводе `render_pages` автоматически.

## 5. Крайние случаи

| Случай | Поведение |
|---|---|
| Наш код отсутствует в списке («выбыл») | Топ-11 + плашка «вашего кода нет в этом списке». |
| Мы №1 (текущая реальность) | «Впереди» пусто → показываем нас + 10 после (11 строк). |
| Меньше 10 строк после нас | Показываем сколько есть (не падаем). |
| Балл отсутствует (`entrance/achievement/final` = None) | «—» в ячейке (через `g()`); `0` показывается как `0` — как на офсайте (напр. «Балл за ИД» = 0). |
| `priority` = None | «—». |
| Нет снапшота у watch | Страница/секция не создаётся (как `has_scores`). |
| Несколько наших кодов в одном списке | Окно вокруг **минимального** (лучшего) места; подсвечиваются все совпавшие строки. Редкий случай, задокументировать. |
| Источник без флагов ВП (не МИРЭА) | Примечание «—», нейтрально. (На практике флаг ставится только на МИРЭА.) |

## 6. Тесты (TDD, до реализации)

**`_gather_neighbors`:**
- Окно строк: наш код на месте N → ровно `[места 1..N] + следующие 10`.
- Мы №1 → 11 строк, наша первая.
- Меньше 10 после → возвращает всё что есть, без ошибки.
- Наш код отсутствует → `we_absent=True`, топ-11.
- Выбор `paid`: платный список → `paid=True`; бюджетный → `paid=False`.
- Нет снапшота → watch пропущен (в результат не попал).
- watch без `track_neighbors` → игнорируется.

**`build_neighbors_html`:**
- Наша строка имеет класс подсветки и маркер «◄ вы».
- Коды выводятся **полностью** (наш код `1366129` присутствует в HTML целиком, не
  замаскирован).
- Заголовок колонки «Платн» для платного, «Согл» для бюджетного.
- Маппинг «Примечание»: `passing_real` → «планируется к зачислению»; только `passing_main` →
  янтарная строка; оба None → «—».
- Пустые баллы → «—».
- `we_absent` → плашка в HTML.

**Конфиг:**
- `track_neighbors` парсится (`true`/отсутствует → `False`).

**`render_pages`:**
- Флагнутый watch со снапшотом → `mirea-list.html` в результате; ссылка `👥 окружение` в
  шапке `index.html`/`table.html`.
- Ни одного флагнутого watch → `mirea-list.html` отсутствует, ссылки нет.

Тесты гермичны: строят `Snapshot`/`Store` из фикстур (config.yaml и state.db gitignored и в
worktree отсутствуют — реальные данные в тестах не используются).

## 7. Вне рамок (YAGNI)

- Без истории/спарклайнов/дельт по соседям (только текущий срез).
- Без сортировки, фильтров, JS на новой странице.
- Без маскировки кодов (выбор пользователя — полные коды).
- Без колонок «Оценки» (попредметно) и «Преим. право».
- Без нового сетевого запроса — только уже сохранённый снапшот.
- Включение флага на live-watch (`config.yaml`) — ручной шаг после мержа (config.yaml
  приватный, в PR не входит).

## 8. Файлы, которые затрагиваем

- `vuz_monitor/config.py` — поле + парсинг `track_neighbors`.
- `vuz_monitor/dashboard.py` — `_gather_neighbors`, `build_neighbors_html`, `_NEIGHBORS_STYLE`,
  `_LINK_LIST`, `link_neighbors` в трёх существующих рендерах, ветка в `render_pages`.
- `config.example.yaml` — документация флага.
- `tests/test_dashboard*.py` (или новый `tests/test_neighbors.py`) — тесты выше.
- `config.yaml` (приватный, вне PR) — включить флаг на live-watch после мержа.
