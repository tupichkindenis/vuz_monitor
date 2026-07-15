"""Static HTML dashboard, generated from ``state.db`` (Phase 1).

One self-contained page: every ВУЗ + основа (бюджет/платно) as a section, each
specialty as a card with the applicant's current standing and two inline-SVG
sparklines (место — inverted axis, балл). No external CSS/JS/CDN, theme-aware.

Single source of truth is ``state.db``: ``generate(config, store)`` reads the last
snapshot per watch, so ``run`` and the standalone ``dashboard`` CLI produce an
identical page offline. Formatters are shared with the notifier via ``format.py``.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .diff import compute_status
from .format import esc, fmt_source_time, g, is_paid, mask_code, pass_real, split_group, yesno
from .models import normalize_code
from .report import BUCKET_WIDTH, CodeReport, WatchReport, group_reports

MSK = ZoneInfo("Europe/Moscow")
STALE_HOURS = 2  # last snapshot older than this → «данные устарели» hint

_SW, _SH, _SPAD = 120, 28, 3  # sparkline viewBox + padding


# --------------------------------------------------------------------------- #
# Generation from state.db
# --------------------------------------------------------------------------- #
def _gather(config, store):
    """Load the latest snapshot per watch → (grouped reports, history dict).
    Shared by both the card page and the table page (one state.db pass)."""
    reports = []
    history = {}  # (watch_id, code_display) -> [daily points]
    for w in config.watches:
        snap = store.load_prev(w.watch_id)
        code_reports = []
        for code in config.resolve_codes(w):
            status = compute_status(snap, code, w.plan_override)
            code_reports.append(CodeReport(status=status, changes=[], first_run=False))
            disp = status.code_display if status is not None else code
            history[(w.watch_id, disp)] = store.load_history(w.watch_id, code)
        reports.append(
            WatchReport(
                name=w.name,
                title=snap.meta.title if snap else None,
                meta=snap.meta if snap else None,
                codes=code_reports,
                group=w.group or w.name,
                watch_id=w.watch_id,
                fetched_at=snap.fetched_at if snap else None,
            )
        )
    return group_reports(reports), history


def generate(config, store, now=None) -> str:
    """The mobile card page (docs/index.html)."""
    groups, history = _gather(config, store)
    return build_html(groups, history, now=now)


def generate_table(config, store, now=None) -> str:
    """The desktop summary table (docs/table.html)."""
    groups, history = _gather(config, store)
    return build_table_html(groups, history, now=now)


def render_pages(config, store, now=None) -> dict:
    """All pages from a single state.db pass: {filename: html}. The score-loading
    page (mirea-scores.html) and the neighbors page (mirea-list.html) are included
    only when their feature has data for at least one competition."""
    groups, history = _gather(config, store)
    specs = _gather_score_progress(config, store)
    neighbors = _gather_neighbors(config, store)
    has_scores = bool(specs)
    has_neighbors = bool(neighbors)
    pages = {
        "index.html": build_html(groups, history, now=now,
                                 link_scores=has_scores, link_neighbors=has_neighbors),
        "table.html": build_table_html(groups, history, now=now,
                                       link_scores=has_scores, link_neighbors=has_neighbors),
    }
    if has_scores:
        pages["mirea-scores.html"] = build_score_progress_html(specs, now=now,
                                                               link_neighbors=has_neighbors)
    if has_neighbors:
        pages["mirea-list.html"] = build_neighbors_html(neighbors, now=now, link_scores=has_scores)
    return pages


def _gather_score_progress(config, store):
    """One spec dict per `track_scores` competition that has recorded history:
    {title, history (raw hourly rows ASC), tracked (ваш номер) | None}."""
    specs = []
    for w in config.watches:
        if not w.track_scores:
            continue
        history = store.load_score_progress(w.watch_id)
        if not history:
            continue
        snap = store.load_prev(w.watch_id)
        title = snap.meta.title if (snap and snap.meta and snap.meta.title) else w.name
        total_now = history[-1]["total"]
        tracked = None
        if snap:
            for code in config.resolve_codes(w):
                e = snap.by_code(code)
                if e is None:
                    continue
                try:
                    bucket = (int(e.code) // BUCKET_WIDTH) * BUCKET_WIDTH
                except (TypeError, ValueError):
                    bucket = None
                tracked = {
                    "code": e.code_display,
                    "place": e.place,
                    "total": total_now,
                    "no_score": (not e.entrance_score) and (not e.is_bvi),
                    "bucket": bucket,
                }
                break
        specs.append({"title": title, "history": history, "tracked": tracked})
    return specs


def _gather_neighbors(config, store):
    """One spec dict per `track_neighbors` competition that has a snapshot:
    {title, updated_at, fetched_at, paid, our_codes, we_absent, rows}. `rows` is the
    FULL filtered list of active applicants in place order, renumbered 1..N at render
    time. The filter depends on competition type: paid → `consent` (API `accepted`,
    «Соблюдены условия для платного»); budget → `passing_real` (API `iHPO`,
    «Проходной ВП»). When our code is not among them, `we_absent=True` (rows still
    hold the full eligible list)."""
    specs = []
    for w in config.watches:
        if not w.track_neighbors:
            continue
        snap = store.load_prev(w.watch_id)
        if snap is None:
            continue
        title = snap.meta.title if (snap.meta and snap.meta.title) else w.name
        our_codes = {normalize_code(c) for c in config.resolve_codes(w)}
        paid = is_paid(title) or is_paid(w.group or w.name)
        ok = (lambda e: e.consent) if paid else (lambda e: e.passing_real)
        eligible = sorted(
            [e for e in snap.entrants
             if e.place is not None and e.is_active and ok(e)],
            key=lambda e: e.place,
        )
        we_absent = not any(e.code in our_codes for e in eligible)
        specs.append({
            "title": title,
            "updated_at": snap.meta.updated_at if snap.meta else None,
            "fetched_at": snap.fetched_at,
            "paid": paid,
            "our_codes": our_codes,
            "we_absent": we_absent,
            "rows": eligible,
        })
    return specs


# --------------------------------------------------------------------------- #
# Time helpers
# --------------------------------------------------------------------------- #
def _parse(ts):
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _age_hours(ts, now):
    dt = _parse(ts)
    return None if dt is None else (now - dt).total_seconds() / 3600.0


def _fetched_msk(ts) -> str:
    dt = _parse(ts)
    return dt.astimezone(MSK).strftime("%d.%m %H:%M") if dt else "—"


def _updated_label(reports) -> str:
    """Header freshness: source's own timestamp if any, else our fetch time (MSK)."""
    src = [r.meta.updated_at for r in reports if r.meta and r.meta.updated_at]
    if src:
        return fmt_source_time(max(src))
    fetched = [r.fetched_at for r in reports if r.fetched_at]
    return _fetched_msk(max(fetched)) if fetched else "—"


# --------------------------------------------------------------------------- #
# Sparkline (inline SVG)
# --------------------------------------------------------------------------- #
def _sparkline(values, higher_is_better: bool, cls: str) -> str:
    """Inline SVG from a daily series. Improvement always trends UP.

    `values` may contain None (code absent that day) → segmented polyline (gaps
    not connected). Flat series → mid line (no div-by-zero). All-None → '' so the
    caller can render a muted dash.
    """
    pts = [v for v in values if v is not None]
    if not pts:
        return ""
    lo, hi = min(pts), max(pts)
    span = hi - lo
    n = len(values)

    def x(i):
        return _SW / 2 if n == 1 else _SPAD + (i / (n - 1)) * (_SW - 2 * _SPAD)

    def y(v):
        t = 0.5 if span == 0 else ((hi - v) / span if higher_is_better else (v - lo) / span)
        return _SPAD + t * (_SH - 2 * _SPAD)

    # Split into runs of consecutive present days.
    segs, cur = [], []
    for i, v in enumerate(values):
        if v is None:
            if cur:
                segs.append(cur)
                cur = []
        else:
            cur.append((i, v))
    if cur:
        segs.append(cur)

    parts = [f'<svg class="spark-svg {cls}" viewBox="0 0 {_SW} {_SH}" aria-hidden="true">']
    for seg in segs:
        if len(seg) == 1:
            i, v = seg[0]
            parts.append(f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="1.8" />')
        else:
            pts_s = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in seg)
            parts.append(f'<polyline points="{pts_s}" fill="none" />')
    li, lv = segs[-1][-1]
    parts.append(f'<circle class="spark-end" cx="{x(li):.1f}" cy="{y(lv):.1f}" r="2.2" />')
    parts.append("</svg>")
    return "".join(parts)


def _spark_row(points) -> str:
    if not points:
        return '<div class="spark empty">📈 копим историю — первые точки за пару дней</div>'
    place_svg = _sparkline([p["place"] for p in points], higher_is_better=False, cls="spark-place")
    score_svg = _sparkline([p["final_score"] for p in points], higher_is_better=True, cls="spark-score")
    place_html = place_svg or '<span class="spark-dash">—</span>'
    score_html = score_svg or '<span class="spark-dash">—</span>'
    return (
        '<div class="spark">'
        f'<div class="spark-item"><span class="spark-cap">место</span>{place_html}</div>'
        f'<div class="spark-item faint"><span class="spark-cap">балл</span>{score_html}</div>'
        "</div>"
    )


# --------------------------------------------------------------------------- #
# Card / group rendering
# --------------------------------------------------------------------------- #
def _pill(text: str, cls: str) -> str:
    return f'<span class="pill {cls}">{esc(text)}</span>'


def _head(name: str, pill_html: str) -> str:
    """Card header: name on the left (clamped to 2 lines with «…» when too long),
    status pill pinned to the top-right corner."""
    return f'<div class="card-head"><div class="spec-name">{name}</div>{pill_html}</div>'


def _card(report, points) -> str:
    name = esc(report.name)
    paid = is_paid(report.title) or is_paid(report.group)

    if report.error:  # defensive; generate() never sets this (reads state.db)
        return (
            f'<div class="card err">'
            + _head(name, _pill("нет свежих данных", "muted"))
            + f'<div class="tertiary muted">⚠️ {esc(report.error)}</div></div>'
        )

    st = report.codes[0].status if report.codes else None

    if st is None:  # source never fetched successfully yet
        return (
            f'<div class="card nodata">'
            + _head(name, _pill("нет данных", "muted"))
            + '<div class="tertiary muted">источник ещё не опрашивался</div>'
            + _spark_row(points) + "</div>"
        )

    if not st.present or st.place is None:  # «выбыл»
        return (
            f'<div class="card absent">'
            + _head(name, _pill("выбыл", "muted"))
            + '<div class="tertiary muted">выбыл из списка</div>'
            + _spark_row(points) + "</div>"
        )

    # present ----------------------------------------------------------------
    if st.passing_real is None and st.passing_main is None:
        # source publishes no ВП flags (e.g. МАИ) — neutral, not «не проходите»
        accent, pill_cls, pill_text = "neutral", "grey", "—"
    elif st.passing_real:
        accent, pill_cls, pill_text = "pass-real", "green", pass_real(st.passing_real)
    elif st.passing_main:
        accent, pill_cls, pill_text = "pass-main", "amber", pass_real(st.passing_real)
    else:
        accent, pill_cls, pill_text = "neutral", "grey", pass_real(st.passing_real)

    place = f"место {st.place}"
    if st.total is not None:
        place += f" из {st.total}"

    secondary = f"приоритет {esc(st.priority)} · балл {g(st.final_score)}"
    if st.plan is not None:
        secondary += f" · всего {st.plan} мест"

    if paid:
        consent_txt = f"Соблюдены условия для платного: {yesno(st.consent)}"
        detail = []
        if st.contract is not None:
            detail.append(f"договор: {yesno(st.contract)}")
        if st.payment is not None:
            detail.append(f"оплата: {yesno(st.payment)}")
        if detail:
            consent_txt += f" ({', '.join(detail)})"
    else:
        consent_txt = f"Согласие: {yesno(st.consent)}"

    tertiary = f"Основной ВП: {yesno(st.passing_main)} · {esc(consent_txt)}"

    return (
        f'<div class="card {accent}">'
        + _head(name, _pill(pill_text, pill_cls))
        + f'<div class="place-line">{esc(place)}</div>'
        + f'<div class="secondary">{secondary}</div>'
        + f'<div class="tertiary">{tertiary}</div>'
        + _spark_row(points) + "</div>"
    )


def _group_axes(name):
    """'МИРЭА — бюджет' -> ('МИРЭА', 'бюджет'). osnova is always 'бюджет'|'платно'."""
    vuz, _ = split_group(name)
    if not vuz:
        vuz = name
    osnova = "платно" if is_paid(name) else "бюджет"
    return vuz, osnova


def _group_section(name, reports, history, now, vuz, osnova) -> str:
    vuz_updated = _updated_label(reports)
    fetched = [r.fetched_at for r in reports if r.fetched_at]
    age = _age_hours(max(fetched), now) if fetched else None
    stale = ""
    if age is not None and age > STALE_HOURS:
        stale = f'<span class="stale">⚠️ данные от {esc(_fetched_msk(max(fetched)))}</span>'

    # «проходите: N/M» over specialties that publish ВП flags; groups without
    # flags at all (МАИ) omit the counter rather than show a misleading «0/M».
    flagged = [
        cr.status for r in reports for cr in r.codes
        if cr.status and cr.status.present and cr.status.passing_real is not None
    ]
    meta_parts = []
    if flagged:
        passing = sum(1 for s in flagged if s.passing_real)
        meta_parts.append(f"проходите: {passing}/{len(flagged)}")
    meta_parts.append(f"обновлено {esc(vuz_updated)}{stale}")

    cards = []
    for r in reports:
        disp = r.codes[0].status.code_display if (r.codes and r.codes[0].status) else None
        pts = history.get((r.watch_id, disp), []) if disp is not None else []
        cards.append(_card(r, pts))

    return (
        f'<section class="group" data-vuz="{esc(vuz)}" data-osnova="{esc(osnova)}">'
        f'<div class="group-header"><span class="group-title">{esc(name)}</span>'
        f'<span class="group-meta">{" · ".join(meta_parts)}</span></div>'
        + "".join(cards)
        + "</section>"
    )


_LINK_TABLE = '<a class="page-link" href="table.html">▦ таблица</a>'
_LINK_CARDS = '<a class="page-link" href="index.html">☰ карточки</a>'
_LINK_SCORES = '<a class="page-link" href="mirea-scores.html">📊 баллы</a>'
_LINK_LIST = '<a class="page-link" href="mirea-list.html">👥 окружение</a>'


def _summary(groups) -> dict:
    """Global counts for the top bar (shared by both pages). «Проходной ВП: N/T»
    counts only specialties that publish ВП flags, so МАИ/Станкин don't inflate T."""
    flat = [r for _, reps in groups for r in reps]
    all_codes = [cr for r in flat for cr in r.codes]
    present = [cr.status for cr in all_codes if cr.status and cr.status.present]
    flagged = [s for s in present if s.passing_real is not None]
    codes = []
    for cr in all_codes:
        if cr.status is not None and cr.status.code_display not in codes:
            codes.append(cr.status.code_display)
    return {
        "who": " / ".join(mask_code(c) for c in codes),
        "n_real": sum(1 for s in flagged if s.passing_real),
        "n_total": len(flagged),
        "n_main": sum(1 for s in flagged if s.passing_main),
        "n_consent": sum(1 for s in present if s.consent),
        "updated": _updated_label(flat),
        "fetched": [r.fetched_at for r in flat if r.fetched_at],
    }


def _summary_bar(groups, now, link_html: str = "") -> str:
    s = _summary(groups)
    age = _age_hours(max(s["fetched"]), now) if s["fetched"] else None
    stale = (
        f' · <span class="stale">данные устарели ({int(age)} ч)</span>'
        if age is not None and age > STALE_HOURS else ""
    )
    who = f'<span class="who">{esc(s["who"])}</span> · ' if s["who"] else ""
    link = f' · {link_html}' if link_html else ""
    return (
        '<div class="summary">' + who
        + f'<b>Проходной ВП: {s["n_real"]}/{s["n_total"]}</b> · Основной ВП: {s["n_main"]} · '
        f'согласий: {s["n_consent"]} · обновлено {esc(s["updated"])}{stale}{link}'
        "</div>"
    )


def build_html(groups, history, now=None, link_scores=False, link_neighbors=False) -> str:
    """Render the full page. `groups` = group_reports() output; `history` =
    {(watch_id, code_display): [daily points]}."""
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    # Axes for the ВУЗ / основа switchers (first-seen order for ВУЗ).
    vuz_order = []
    osn_present = set()
    for name, _ in groups:
        v, o = _group_axes(name)
        if v not in vuz_order:
            vuz_order.append(v)
        osn_present.add(o)
    osn_order = [o for o in ("бюджет", "платно") if o in osn_present]

    sections = "".join(
        _group_section(name, reps, history, now, *_group_axes(name)) for name, reps in groups
    ) or '<p class="empty">Нет отслеживаемых списков.</p>'

    filters = _filter_bar(vuz_order, osn_order)

    return (
        "<!doctype html>\n"
        '<html lang="ru"><head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="robots" content="noindex, nofollow">\n'
        "<title>ВУЗ-мониторинг</title>\n"
        f"<style>{_STYLE}</style>\n"
        "</head><body>\n"
        '<div class="wrap">\n'
        '<div class="topbar">'
        + _summary_bar(groups, now, _LINK_TABLE
                       + (" " + _LINK_SCORES if link_scores else "")
                       + (" " + _LINK_LIST if link_neighbors else ""))
        + filters
        + "</div>\n"
        + _LEGEND + "\n"
        '<p class="no-match" hidden>Нет списков под выбранный фильтр.</p>\n'
        f"{sections}\n"
        '<footer class="foot">обновляется каждый час · vuz_monitor</footer>\n'
        "</div>\n"
        f"<script>{_SCRIPT}</script>\n"
        "</body></html>\n"
    )


def _chip(val, label) -> str:
    return f'<button type="button" class="chip" data-val="{esc(val)}">{esc(label)}</button>'


def _filter_bar(vuz_order, osn_order) -> str:
    """Two rows of single-select toggle chips (ВУЗ / основа). A row is omitted
    when it would offer only one choice."""
    osn_labels = {"бюджет": "Бюджет", "платно": "Платно"}
    rows = ""
    if len(vuz_order) > 1:
        chips = _chip("__all__", "Все") + "".join(_chip(v, v) for v in vuz_order)
        rows += f'<div class="filter-row" data-dim="vuz"><span class="filter-lbl">ВУЗ</span>{chips}</div>'
    if len(osn_order) > 1:
        chips = _chip("__all__", "Все") + "".join(_chip(o, osn_labels.get(o, o)) for o in osn_order)
        rows += f'<div class="filter-row" data-dim="osnova"><span class="filter-lbl">Основа</span>{chips}</div>'
    return f'<div class="filters">{rows}</div>' if rows else ""


# --------------------------------------------------------------------------- #
# Desktop summary table (docs/table.html)
# --------------------------------------------------------------------------- #
def _num_td(v, disp=None) -> str:
    """Right-aligned numeric cell with data-sort; None → «—» (sorts last)."""
    if v is None:
        return '<td class="num muted">—</td>'
    return f'<td class="num" data-sort="{v}">{esc(disp if disp is not None else v)}</td>'


def _delta_td(history, watch_id, disp) -> str:
    """Day-over-day place change from history: ▲ improved / ▼ dropped."""
    pts = history.get((watch_id, disp), []) if disp is not None else []
    places = [p["place"] for p in pts if p["place"] is not None]
    if len(places) < 2:
        return '<td class="num muted"></td>'
    d = places[-2] - places[-1]           # +ve = moved up (place decreased)
    if d > 0:
        return f'<td class="num up" data-sort="{d}">▲{d}</td>'
    if d < 0:
        return f'<td class="num down" data-sort="{d}">▼{-d}</td>'
    return '<td class="num muted" data-sort="0">·</td>'


_EMPTY6 = ('<td class="num muted">—</td><td class="num muted">—</td>'
           '<td class="num muted">—</td><td class="muted">—</td>'
           '<td class="muted">—</td><td class="muted">—</td>')


def _spark_cell(points) -> str:
    """Tiny place sparkline (inverted axis) from history, for the Тренд column."""
    svg = _sparkline([p["place"] for p in points], higher_is_better=False, cls="spark-place")
    return f'<td class="spark">{svg}</td>' if svg else '<td class="spark muted">—</td>'


def _table_row(report, vuz, osnova, history) -> str:
    name = esc(report.name)
    st = report.codes[0].status if report.codes else None
    disp = st.code_display if st is not None else None
    attrs = f' data-vuz="{esc(vuz)}" data-osnova="{esc(osnova)}"'
    head = f'<td>{esc(vuz)}</td><td>{esc(osnova)}</td>'
    name_td = f'<td class="name" title="{name}">{name}</td>'
    pts = history.get((report.watch_id, disp), []) if disp is not None else []
    tail = (
        _delta_td(history, report.watch_id, disp)
        + _spark_cell(pts)
        + f'<td class="upd">{esc(fmt_source_time(report.meta.updated_at) if report.meta else "—")}</td>'
    )

    if st is None:  # source never fetched
        return (f'<tr class="nodata"{attrs}>{head}<td class="num muted">—</td>{name_td}'
                f'<td class="muted">нет данных</td>{_EMPTY6}{tail}</tr>')

    if not st.present or st.place is None:  # «выбыл»
        return (f'<tr class="absent"{attrs}>{head}{_num_td(st.priority)}{name_td}'
                f'<td class="muted" data-sort="">выбыл</td>{_EMPTY6}{tail}</tr>')

    if st.passing_real is None and st.passing_main is None:   # no ВП flags (МАИ)
        accent, preal = "neutral", '<span class="muted">—</span>'
    elif st.passing_real:
        accent, preal = "pass-real", f'<span class="ok">{esc(pass_real(True))}</span>'
    elif st.passing_main:
        accent, preal = "pass-main", esc(pass_real(st.passing_real))
    else:
        accent, preal = "neutral", esc(pass_real(st.passing_real))
    pmain = "—" if st.passing_main is None else yesno(st.passing_main)

    return (
        f'<tr class="{accent}"{attrs}>{head}{_num_td(st.priority)}{name_td}'
        + _num_td(st.place) + _num_td(st.total) + _num_td(st.plan)
        + _num_td(st.final_score, disp=g(st.final_score))
        + f'<td class="preal">{preal}</td>'
        + f'<td>{esc(pmain)}</td><td>{esc(yesno(st.consent))}</td>'
        + tail + "</tr>"
    )


# (label, kind): kind = "num" (sort numerically by data-sort) / "text" (sort by
# text) / "nosort" (not sortable, e.g. the sparkline column).
_TABLE_HEADERS = [
    ("ВУЗ", "text"), ("Основа", "text"), ("Приор", "num"), ("Специальность", "text"),
    ("Место", "num"), ("из", "num"), ("Мест", "num"), ("Балл", "num"),
    ("Прох.ВП", "text"), ("Осн.ВП", "text"), ("Согл/Дог", "text"), ("Δ", "num"),
    ("Тренд", "nosort"), ("Обновлено", "text"),
]


def build_table_html(groups, history, now=None, link_scores=False, link_neighbors=False) -> str:
    """Desktop one-table view: row = specialty, columns = all params, sortable,
    with ВУЗ/основа filter chips and a place-trend sparkline column."""
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    vuz_order = []
    osn_present = set()
    for name, _ in groups:
        v, o = _group_axes(name)
        if v not in vuz_order:
            vuz_order.append(v)
        osn_present.add(o)
    osn_order = [o for o in ("бюджет", "платно") if o in osn_present]

    rows = [
        _table_row(r, *_group_axes(name), history)
        for name, reps in groups for r in reps
    ]
    tbody = "".join(rows) or f'<tr><td colspan="{len(_TABLE_HEADERS)}" class="empty">Нет данных.</td></tr>'

    def _th(h, kind):
        attr = " data-num" if kind == "num" else " data-nosort" if kind == "nosort" else ""
        return f"<th{attr}>{esc(h)}</th>"

    thead = "".join(_th(h, k) for h, k in _TABLE_HEADERS)
    filters = _filter_bar(vuz_order, osn_order)   # shared with the card page
    return (
        "<!doctype html>\n"
        '<html lang="ru"><head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="robots" content="noindex, nofollow">\n'
        "<title>ВУЗ-мониторинг · таблица</title>\n"
        f"<style>{_TABLE_STYLE}</style>\n"
        "</head><body>\n"
        '<div class="wrap-wide">\n'
        '<div class="topbar">' + _summary_bar(groups, now, _LINK_CARDS
            + (" " + _LINK_SCORES if link_scores else "")
            + (" " + _LINK_LIST if link_neighbors else "")) + filters + "</div>\n"
        '<p class="no-match" hidden>Нет строк под выбранный фильтр.</p>\n'
        '<div class="table-scroll"><table id="grid"><thead><tr>'
        + thead + "</tr></thead><tbody>\n" + tbody + "\n</tbody></table></div>\n"
        '<footer class="foot">обновляется каждый час · клик по заголовку — сортировка · vuz_monitor</footer>\n'
        "</div>\n"
        f"<script>{_TABLE_SCRIPT}</script>\n"
        "</body></html>\n"
    )


# Collapsible legend explaining the ВП flags + pill colours (collapsed by default).
# --------------------------------------------------------------------------- #
# Score-loading tracker page (docs/mirea-scores.html)
# --------------------------------------------------------------------------- #
def _grp(n) -> str:
    """Thousands-grouped integer with a non-breaking space (1266 -> '1 266')."""
    if n is None:
        return "—"
    return f"{int(n):,}".replace(",", " ")


def _delta_html(new, old, good_down=True, mark: bool = True) -> str:
    """Signed change with %. `good_down`: is a DECREASE the good direction (as for
    «без баллов»)? Pass ``None`` for a neutral (informational) delta with no
    good/bad colour. A good, nonzero move gets a ✅ when `mark`."""
    if old is None:
        return '<span class="delta muted">—</span>'
    d = new - old
    if d == 0:
        return '<span class="delta muted">0</span>'
    pct = f" ({d / old * 100:+.1f}%)" if old else ""   # old==0 → % undefined, show count only
    if good_down is None:
        return f'<span class="delta muted">{d:+d}{pct}</span>'
    good = (d < 0) if good_down else (d > 0)
    tick = " ✅" if (good and mark) else ""
    return f'<span class="delta {"good" if good else "bad"}">{d:+d}{pct}{tick}</span>'


# Fixed intraday comparison slots (MSK clock hours) for the score page.
SCORE_SLOTS = [("10:00", 10), ("14:00", 14), ("19:00", 19)]  # утро / день / вечер


def _score_slots(history, now):
    """Fixed comparison points: «конец вчера» + today's clock slots (MSK).
    Returns [{label, dt, row}]; `row` is None when the slot is still in the future
    or has no snapshot. A past slot carries the freshest snapshot at/before it (so
    it reads «данные на 14:00», carried forward if МИРЭА hasn't republished)."""
    now_msk = now.astimezone(MSK)
    today = now_msk.date()
    yesterday = today - timedelta(days=1)
    parsed = []
    for r in history:
        dt = _parse(r["ts"])
        if dt is not None:
            parsed.append((dt.astimezone(MSK), r))
    parsed.sort(key=lambda x: x[0])

    def last_where(pred):
        found = None
        for dt, r in parsed:
            if pred(dt):
                found = (dt, r)
        return found

    y = last_where(lambda dt: dt.date() == yesterday)
    slots = [{"label": "конец вчера", "dt": y[0] if y else None, "row": y[1] if y else None}]
    for label, hh in SCORE_SLOTS:
        target = datetime.combine(today, time(hh, 0), tzinfo=MSK)
        if target > now_msk:                       # slot hasn't happened yet
            slots.append({"label": label, "dt": None, "row": None})
            continue
        s = last_where(lambda dt, t=target: dt.date() == today and dt <= t)
        slots.append({"label": label, "dt": s[0] if s else None, "row": s[1] if s else None})
    return slots


def _daily_no_score(history):
    """«без баллов» count per MSK day (last observation of the day wins), for the
    trend sparkline. Raw hourly rows in → one value per day out."""
    by_day = {}
    for r in history:
        dt = _parse(r["ts"])
        if dt is None:
            continue
        by_day[dt.astimezone(MSK).date().isoformat()] = r["no_score"]
    return [by_day[d] for d in sorted(by_day)]


def _slot_cell(row, getter):
    """A numeric cell for one slot; «—» when the slot has no data (future/absent)."""
    return f'<td class="num">{_grp(getter(row)) if row is not None else "—"}</td>'


def _score_section(spec, now) -> str:
    hist = spec["history"]
    tracked = spec.get("tracked")
    slots = _score_slots(hist, now)
    yrow = slots[0]["row"]                                    # конец вчера
    today_last = next((s["row"] for s in reversed(slots[1:]) if s["row"] is not None), None)

    # header cells (slot label + the actual snapshot time under it). `dt` is an
    # MSK-aware datetime already, so format it directly (not via _fetched_msk,
    # which parses a ts string).
    def _hdr(s):
        sub = s["dt"].strftime("%d.%m %H:%M") if (s["row"] is not None and s["dt"]) else "—"
        return f'<th class="num">{esc(s["label"])}<br><span class="ts">{esc(sub)}</span></th>'
    slot_hdrs = "".join(_hdr(s) for s in slots)

    def _summary_row(label, key, good_down):
        cells = "".join(_slot_cell(s["row"], lambda r, k=key: r[k]) for s in slots)
        if today_last is not None and yrow is not None:
            delta = _delta_html(today_last[key], yrow[key], good_down=good_down)
        else:
            delta = '<span class="delta muted">—</span>'
        return f'<tr><td>{label}</td>{cells}<td class="num">{delta}</td></tr>'

    summary = (
        '<div class="cmp-scroll"><table class="cmp"><thead><tr><th>Показатель</th>'
        + slot_hdrs + '<th class="num">Изменение</th></tr></thead><tbody>'
        + _summary_row("Всего без баллов", "no_score", True)
        + _summary_row("Всего заявок в списке", "total", None)
        + "</tbody></table></div>"
    )

    buckets = set()
    for s in slots:
        if s["row"] is not None:
            buckets |= set(s["row"]["buckets"])

    def _ns(row, b):
        return row["buckets"].get(b, [0, 0])[1]

    rows = []
    for b in sorted(buckets):
        # «Всего» in this range = the freshest slot that has this bucket
        total_b = 0
        for s in reversed(slots):
            if s["row"] is not None and b in s["row"]["buckets"]:
                total_b = s["row"]["buckets"][b][0]
                break
        cells = "".join(_slot_cell(s["row"], lambda r, bb=b: _ns(r, bb)) for s in slots)
        y_ns = _ns(yrow, b) if yrow is not None else None
        t_ns = _ns(today_last, b) if today_last is not None else None
        delta = (_delta_html(t_ns, y_ns) if (t_ns is not None and y_ns is not None)
                 else '<span class="delta muted">—</span>')
        you = bool(tracked and tracked.get("bucket") == b)
        tr_cls = ' class="you"' if you else ""
        you_mark = " ◄ ваш" if you else ""
        rows.append(
            f"<tr{tr_cls}>"
            f'<td>{_grp(b)}–{_grp(b + BUCKET_WIDTH - 1)}{you_mark}</td>'
            f'<td class="num">{_grp(total_b)}</td>{cells}'
            f'<td class="num">{delta}</td></tr>'
        )
    range_hdrs = "".join(f'<th class="num">{esc(s["label"])}</th>' for s in slots)
    ranges = (
        '<div class="ranges-scroll"><table class="ranges"><thead><tr>'
        '<th>Диапазон номера</th><th class="num">Всего</th>' + range_hdrs
        + '<th class="num">Изм</th></tr></thead><tbody>'
        + "".join(rows) + "</tbody></table></div>"
    )

    spark = _sparkline(_daily_no_score(hist), higher_is_better=False, cls="spark-place")
    trend = (
        '<div class="trend"><span class="trend-cap">тренд «без баллов»</span>'
        + (spark or '<span class="spark-dash">копим историю…</span>') + "</div>"
    )

    yourblock = ""
    if tracked:
        st = "без баллов" if tracked["no_score"] else "балл подгружен ✅"
        where = ""
        if tracked["place"] is not None:
            place = f'место {tracked["place"]}' + (f' из {_grp(tracked["total"])}' if tracked["total"] else "")
            where = esc(place) + " — "
        # mask the код участника: this page is public (gh-pages), same convention
        # as every other page — harder to tie the номер to a person via the URL.
        yourblock = (
            f'<div class="yournum">Ваш номер <b>{esc(mask_code(tracked["code"]))}</b>: '
            f'{where}<b>{esc(st)}</b></div>'
        )

    return (
        f'<section class="score-sec"><h2>{esc(spec["title"])}</h2>'
        '<div class="caption">по данным одного конкурса (не по всей МИРЭА)</div>'
        + yourblock + summary
        + "<h3>Распределение по диапазонам номеров</h3>" + ranges + trend
        + "</section>"
    )


def build_score_progress_html(specialties, now=None, link_neighbors=False) -> str:
    """docs/mirea-scores.html — score-loading tracker: intraday comparison
    (конец вчера · 10:00 · 14:00 · 19:00 · Изменение), range distribution across
    the same slots, and a trend sparkline, per tracked competition."""
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    sections = "".join(_score_section(s, now) for s in specialties) or \
        '<p class="empty">Нет отслеживаемых конкурсов.</p>'
    links = _LINK_CARDS + " " + _LINK_TABLE + (" " + _LINK_LIST if link_neighbors else "")
    return (
        "<!doctype html>\n"
        '<html lang="ru"><head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="robots" content="noindex, nofollow">\n'
        "<title>ВУЗ-мониторинг · подгрузка баллов</title>\n"
        f"<style>{_SCORE_STYLE}</style>\n"
        "</head><body>\n"
        '<div class="wrap">\n'
        '<div class="topbar"><div class="summary"><b>Подгрузка баллов испытаний</b> · '
        + links + "</div></div>\n"
        f"{sections}\n"
        '<footer class="foot">обновляется каждый час · vuz_monitor</footer>\n'
        "</div>\n</body></html>\n"
    )


_SCORE_STYLE = """
:root{--bg:#f5f6f8;--card:#fff;--fg:#1a1d21;--muted:#6b7280;--border:#e5e7eb;--green:#15803d;--red:#b91c1c;--accent:#2563eb;--you:#fef9c3;}
@media (prefers-color-scheme:dark){:root{--bg:#0f1216;--card:#171b21;--fg:#e6e8eb;--muted:#9aa4b2;--border:#252b33;--green:#4ade80;--red:#f87171;--accent:#60a5fa;--you:#3f3a12;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);line-height:1.4;font:15px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}
.wrap{max-width:760px;margin:0 auto;padding:12px;}
.topbar{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--border);padding:8px 0;margin-bottom:12px;z-index:5;}
.summary{font-size:14px;}
.page-link{color:var(--accent);text-decoration:none;margin-left:8px;font-size:13px;}
.score-sec{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;margin-bottom:16px;}
.score-sec h2{font-size:16px;margin:0 0 2px;}
.score-sec h3{font-size:13px;color:var(--muted);margin:16px 0 6px;font-weight:600;}
.caption{font-size:12px;color:var(--muted);margin-bottom:10px;}
.yournum{font-size:14px;background:var(--you);border-radius:8px;padding:8px 10px;margin-bottom:12px;}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;}
.cmp-scroll,.ranges-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;}
.cmp,.ranges{font-size:13px;}
.cmp th,.ranges th{text-align:left;color:var(--muted);font-weight:600;font-size:11px;border-bottom:1px solid var(--border);padding:5px 6px;white-space:nowrap;}
.cmp td,.ranges td{padding:5px 6px;border-bottom:1px solid var(--border);white-space:nowrap;}
.num{text-align:right;}
.cmp th.num,.ranges th.num{text-align:right;}
.ts{font-weight:400;color:var(--muted);font-size:10px;}
tr.you{background:var(--you);}
.delta.good{color:var(--green);}
.delta.bad{color:var(--red);}
.delta.muted,.muted{color:var(--muted);}
.trend{display:flex;align-items:center;gap:8px;margin-top:12px;}
.trend-cap{font-size:11px;color:var(--muted);}
.spark-svg{width:120px;height:28px;}
.spark-place polyline{stroke:var(--accent);stroke-width:1.6;fill:none;stroke-linejoin:round;stroke-linecap:round;}
.spark-place circle{fill:var(--accent);}
.spark-dash{color:var(--muted);font-size:12px;}
.foot{font-size:11px;color:var(--muted);text-align:center;margin-top:16px;}
.empty{color:var(--muted);}
"""


# --------------------------------------------------------------------------- #
# Neighbors list page (docs/mirea-list.html)
# --------------------------------------------------------------------------- #
def _note(e) -> str:
    """«Примечание» text from the official passing flags."""
    if e.passing_real:
        return "планируется к зачислению"
    return "—"


def _neighbor_row(e, seq, our_codes, paid) -> str:
    ours = e.code in our_codes
    if ours:
        tr_cls = "you"
    elif e.passing_real:
        tr_cls = "pass-real"
    elif e.passing_main:
        tr_cls = "pass-main"
    else:
        tr_cls = ""
    num = f'{esc(seq)}{" ◄ вы" if ours else ""}'
    flag = e.consent
    cls_attr = f' class="{tr_cls}"' if tr_cls else ""
    return (
        f"<tr{cls_attr}>"
        f'<td class="num">{num}</td>'
        f'<td class="code">{esc(e.code_display)}</td>'
        f'<td class="num">{esc(e.priority) if e.priority is not None else "—"}</td>'
        f"<td>{esc(yesno(flag))}</td>"
        f'<td class="num">{esc(g(e.entrance_score))}</td>'
        f'<td class="num">{esc(g(e.achievement_score))}</td>'
        f'<td class="num">{esc(g(e.final_score))}</td>'
        f"<td>{esc(_note(e))}</td>"
        "</tr>"
    )


def _neighbor_section(spec, now) -> str:
    when = fmt_source_time(spec["updated_at"]) if spec["updated_at"] else _fetched_msk(spec["fetched_at"])
    paid = spec["paid"]
    flag_hdr = "Платн" if paid else "Согл"
    label = "Платно" if paid else "Бюджет"
    empty_txt = ("Пока никто не выполнил условия для платного."
                 if paid else "Пока никто не проходит по Проходному ВП.")
    absent_txt = ("вашего кода нет среди выполнивших условия для платного"
                  if paid else "вашего кода нет среди проходящих по Проходному ВП")
    h2 = f'<h2>{esc(label)} · {esc(spec["title"])}</h2>'
    if not spec["rows"]:
        return (
            f'<section class="nb-sec">{h2}'
            f'<div class="caption">список по состоянию на {esc(when)}</div>'
            f'<p class="empty">{esc(empty_txt)}</p>'
            "</section>"
        )
    banner = (f'<div class="banner">{esc(absent_txt)}</div>'
              if spec["we_absent"] else "")
    head = (
        "<thead><tr>"
        '<th class="num">№</th><th>Код</th><th class="num">Приор</th>'
        f"<th>{esc(flag_hdr)}</th>"
        '<th class="num">ВИ</th><th class="num">ИД</th><th class="num">Σбалл</th>'
        "<th>Примечание</th></tr></thead>"
    )
    body = "".join(
        _neighbor_row(e, i, spec["our_codes"], paid)
        for i, e in enumerate(spec["rows"], 1)
    )
    return (
        f'<section class="nb-sec">{h2}'
        f'<div class="caption">список по состоянию на {esc(when)}</div>'
        + banner
        + '<div class="nb-scroll"><table class="nb">'
        + head + "<tbody>" + body + "</tbody></table></div>"
        + "</section>"
    )


def build_neighbors_html(specs, now=None, link_scores=False) -> str:
    """docs/mirea-list.html — «окружение»: одна секция на каждый track_neighbors
    конкурс. Фильтр зависит от типа: платный — «Соблюдены условия для платного»
    (consent = API accepted); бюджетный — «Проходной ВП» (passing_real = API iHPO).
    Только активные, со сквозной нумерацией 1..N — раскладка офсайта, наша строка
    подсвечена, коды показаны полностью."""
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    sections = "".join(_neighbor_section(s, now) for s in specs) or \
        '<p class="empty">Нет отслеживаемых списков.</p>'
    links = _LINK_CARDS + " " + _LINK_TABLE + (" " + _LINK_SCORES if link_scores else "")
    return (
        "<!doctype html>\n"
        '<html lang="ru"><head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="robots" content="noindex, nofollow">\n'
        "<title>ВУЗ-мониторинг · окружение</title>\n"
        f"<style>{_NEIGHBORS_STYLE}</style>\n"
        "</head><body>\n"
        '<div class="wrap">\n'
        '<div class="topbar"><div class="summary"><b>Окружение в списке</b> · '
        + links + "</div></div>\n"
        f"{sections}\n"
        '<footer class="foot">обновляется каждый час · конкурсы МИРЭА · vuz_monitor</footer>\n'
        "</div>\n</body></html>\n"
    )


_NEIGHBORS_STYLE = """
:root{--bg:#f5f6f8;--card:#fff;--fg:#1a1d21;--muted:#6b7280;--border:#e5e7eb;--green:#15803d;--amber:#b45309;--accent:#2563eb;--you:#fef9c3;--row-green:rgba(34,197,94,.10);--row-amber:rgba(245,158,11,.12);}
@media (prefers-color-scheme:dark){:root{--bg:#0f1216;--card:#171b21;--fg:#e6e8eb;--muted:#9aa4b2;--border:#252b33;--green:#4ade80;--amber:#fbbf24;--accent:#60a5fa;--you:#3f3a12;--row-green:rgba(34,197,94,.13);--row-amber:rgba(245,158,11,.13);}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);line-height:1.4;font:14px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}
.wrap{max-width:900px;margin:0 auto;padding:12px;}
.topbar{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--border);padding:8px 0;margin-bottom:12px;z-index:5;}
.summary{font-size:14px;}
.page-link{color:var(--accent);text-decoration:none;margin-left:8px;font-size:13px;}
.nb-sec{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;margin-bottom:16px;}
.nb-sec h2{font-size:16px;margin:0 0 2px;}
.caption{font-size:12px;color:var(--muted);margin-bottom:10px;}
.banner{font-size:13px;background:var(--you);border-radius:8px;padding:8px 10px;margin-bottom:10px;}
.nb-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;}
table.nb{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:13px;}
.nb th{text-align:left;color:var(--muted);font-weight:600;font-size:11px;border-bottom:1px solid var(--border);padding:6px 8px;white-space:nowrap;}
.nb td{padding:6px 8px;border-bottom:1px solid var(--border);white-space:nowrap;}
.nb .num{text-align:right;}
.nb th.num{text-align:right;}
.nb td.code{font-variant-numeric:tabular-nums;}
.nb tbody tr.pass-real{background:var(--row-green);}
.nb tbody tr.pass-main{background:var(--row-amber);}
.nb tbody tr.you{background:var(--you);font-weight:600;}
.foot{font-size:11px;color:var(--muted);text-align:center;margin-top:16px;}
.empty{color:var(--muted);}
"""


_LEGEND = (
    '<details class="legend">'
    "<summary>Что такое ВП · обозначения</summary>"
    '<div class="legend-body">'
    "<p><b>ВП — высший приоритет:</b> проходите ли вы на направление с учётом ваших "
    "приоритетов по всем программам сразу. Пилюля в правом углу карточки показывает "
    "<b>Проходной ВП</b> (как сейчас), а строка «Основной ВП» — базовый расчёт.</p>"
    '<div class="legend-row"><span class="pill green">проходите</span>'
    "<span><b>Проходной ВП</b> — проходите прямо <b>сейчас</b>, по текущим согласиям "
    "(кто уже принёс согласие на зачисление).</span></div>"
    '<div class="legend-row"><span class="pill amber">не проходите</span>'
    "<span>Янтарный = <b>Основной ВП «да», Проходной «нет»</b>: по баллам вы в пределах "
    "мест, но впереди хватает людей с согласиями. Станете проходным, когда подадите "
    "согласие вовремя или конкуренты уберут своё — пограничное состояние.</span></div>"
    '<div class="legend-row"><span class="pill grey">не проходите</span>'
    "<span>Серый = оба флага «нет», пока не проходите.</span></div>"
    '<div class="legend-row"><span class="pill grey">—</span>'
    "<span>Прочерк = ВУЗ не публикует флаги ВП (напр. МАИ) — смотрите место, балл, "
    "приоритет; «проходите/не проходите» не определить.</span></div>"
    "</div></details>"
)


_STYLE = """
* { box-sizing: border-box; }
:root {
  --bg:#f5f6f8; --card:#ffffff; --fg:#1a1d21; --muted:#6b7280; --border:#e5e7eb;
  --green:#15803d; --green-bd:#22c55e; --amber:#b45309; --amber-bd:#f59e0b;
  --red:#b91c1c; --accent:#2563eb; --pill-grey:#e5e7eb; --pill-grey-fg:#374151;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg:#0f1216; --card:#171b21; --fg:#e6e8eb; --muted:#9aa4b2; --border:#252b33;
    --green:#4ade80; --green-bd:#22c55e; --amber:#fbbf24; --amber-bd:#f59e0b;
    --red:#f87171; --accent:#60a5fa; --pill-grey:#252b33; --pill-grey-fg:#c4ccd6;
  }
}
body {
  margin:0; background:var(--bg); color:var(--fg); line-height:1.35;
  font-family:-apple-system, system-ui, "Segoe UI", Roboto, sans-serif;
  -webkit-font-smoothing:antialiased;
}
.wrap { max-width:640px; margin:0 auto; padding:0 12px 40px; }
[hidden] { display:none !important; }
.topbar {
  position:sticky; top:0; z-index:4; margin:0 -12px 12px; padding:10px 12px;
  background:var(--bg); border-bottom:1px solid var(--border);
}
.summary { font-size:13px; }
.summary .who { color:var(--muted); font-variant-numeric:tabular-nums; }
.page-link { color:var(--accent); text-decoration:none; font-weight:600; white-space:nowrap; }
.filters { display:flex; flex-direction:column; gap:6px; margin-top:8px; }
.filter-row { display:flex; flex-wrap:wrap; align-items:center; gap:6px; }
.filter-lbl { font-size:11px; color:var(--muted); min-width:42px; }
.chip {
  font:inherit; font-size:13px; line-height:1; padding:6px 12px; border-radius:999px;
  border:1px solid var(--border); background:var(--card); color:var(--fg);
  cursor:pointer; -webkit-appearance:none; appearance:none;
}
.chip.active { background:var(--accent); border-color:var(--accent); color:#fff; }
.no-match { color:var(--muted); font-size:14px; padding:16px 0; }
.legend { margin:0 0 16px; border:1px solid var(--border); border-radius:8px; background:var(--card); }
.legend > summary {
  cursor:pointer; padding:9px 12px; font-size:13px; font-weight:600;
  list-style:none; color:var(--muted);
}
.legend > summary::-webkit-details-marker { display:none; }
.legend > summary::before { content:"ℹ️ "; }
.legend[open] > summary { border-bottom:1px solid var(--border); }
.legend-body { padding:10px 12px; font-size:13px; display:flex; flex-direction:column; gap:8px; }
.legend-body p { margin:0; }
.legend-row { display:flex; gap:8px; align-items:flex-start; }
.legend-row .pill { margin-top:1px; }
.stale { color:var(--red); }
.group { margin-bottom:20px; }
.group-header {
  position:sticky; top:var(--topbar-h, 96px); z-index:2; display:flex; flex-wrap:wrap;
  align-items:baseline; gap:4px 10px; padding:6px 0;
  background:var(--bg); border-bottom:1px solid var(--border);
}
.group-title { font-size:18px; font-weight:600; }
.group-meta { font-size:12px; color:var(--muted); }
.card {
  background:var(--card); border:1px solid var(--border); border-left:3px solid var(--border);
  border-radius:8px; padding:12px; margin-top:8px;
}
.card.pass-real { border-left-color:var(--green-bd); }
.card.pass-main { border-left-color:var(--amber-bd); }
.card.absent, .card.nodata, .card.err { opacity:.75; }
.card.err { border-left-color:var(--red); }
.card-head { display:flex; align-items:flex-start; gap:8px; }
.spec-name {
  flex:1 1 auto; min-width:0; font-size:15px; font-weight:600;
  display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:2;
  overflow:hidden; overflow-wrap:anywhere;
}
.place-line { font-size:13px; color:var(--muted); margin-top:5px; }
.pill {
  flex:0 0 auto; font-size:12px; font-weight:600; padding:2px 8px;
  border-radius:999px; white-space:nowrap;
}
.pill.green { color:#fff; background:var(--green); }
.pill.amber { color:#1a1d21; background:var(--amber-bd); }
.pill.grey, .pill.muted { color:var(--pill-grey-fg); background:var(--pill-grey); }
.secondary { font-size:13px; margin-top:4px; }
.tertiary { font-size:13px; margin-top:2px; }
.card.pass-real .pill.green { }
.muted { color:var(--muted); }
.spark { display:flex; gap:16px; margin-top:8px; }
.spark.empty { font-size:12px; color:var(--muted); }
.spark-item { display:flex; align-items:center; gap:6px; }
.spark-cap { font-size:11px; color:var(--muted); }
.spark-dash { color:var(--muted); }
.spark-svg { width:120px; height:28px; overflow:visible; }
.spark-place polyline { stroke:var(--accent); stroke-width:1.7; stroke-linejoin:round; stroke-linecap:round; }
.spark-place circle { fill:var(--accent); }
.spark-item.faint { opacity:.55; }
.spark-score polyline { stroke:var(--muted); stroke-width:1.3; stroke-linejoin:round; stroke-linecap:round; }
.spark-score circle { fill:var(--muted); }
.foot { font-size:11px; color:var(--muted); text-align:center; margin-top:16px; }
.empty { color:var(--muted); font-size:14px; }
"""


# Progressive enhancement: with JS the chips filter sections by ВУЗ + основа
# (single-select per row, choice remembered in localStorage). Without JS every
# section stays visible — the page degrades to the full list.
_SCRIPT = """
(function () {
  var rows = Array.prototype.slice.call(document.querySelectorAll('.filter-row'));
  var sections = Array.prototype.slice.call(document.querySelectorAll('section.group'));
  var noMatch = document.querySelector('.no-match');
  var topbar = document.querySelector('.topbar');
  if (!rows.length || !sections.length) return;

  function hasChip(dim, val) {
    var chips = document.querySelectorAll('.filter-row[data-dim="' + dim + '"] .chip');
    for (var i = 0; i < chips.length; i++) {
      if (chips[i].getAttribute('data-val') === val) return true;
    }
    return false;
  }
  function firstVal(dim) {
    var chips = document.querySelectorAll('.filter-row[data-dim="' + dim + '"] .chip');
    for (var i = 0; i < chips.length; i++) {
      var v = chips[i].getAttribute('data-val');
      if (v !== '__all__') return v;
    }
    return '__all__';
  }

  var state = { vuz: firstVal('vuz'), osnova: '__all__' };
  try {
    var saved = JSON.parse(localStorage.getItem('vuz_filter') || '{}');
    if (saved.vuz) state.vuz = saved.vuz;
    if (saved.osnova) state.osnova = saved.osnova;
  } catch (e) {}
  if (state.vuz !== '__all__' && !hasChip('vuz', state.vuz)) state.vuz = firstVal('vuz');
  if (state.osnova !== '__all__' && !hasChip('osnova', state.osnova)) state.osnova = '__all__';

  function apply() {
    var any = false;
    sections.forEach(function (sec) {
      var v = sec.getAttribute('data-vuz'), o = sec.getAttribute('data-osnova');
      var show = (state.vuz === '__all__' || state.vuz === v) &&
                 (state.osnova === '__all__' || state.osnova === o);
      sec.hidden = !show;
      if (show) any = true;
    });
    if (noMatch) noMatch.hidden = any;
    rows.forEach(function (row) {
      var dim = row.getAttribute('data-dim');
      Array.prototype.forEach.call(row.querySelectorAll('.chip'), function (ch) {
        ch.classList.toggle('active', ch.getAttribute('data-val') === state[dim]);
      });
    });
    try { localStorage.setItem('vuz_filter', JSON.stringify(state)); } catch (e) {}
  }

  rows.forEach(function (row) {
    var dim = row.getAttribute('data-dim');
    row.addEventListener('click', function (e) {
      var ch = e.target && e.target.closest ? e.target.closest('.chip') : null;
      if (!ch) return;
      state[dim] = ch.getAttribute('data-val');
      apply();
    });
  });

  function setOffset() {
    if (topbar) document.documentElement.style.setProperty('--topbar-h', topbar.offsetHeight + 'px');
  }
  setOffset();
  window.addEventListener('resize', setOffset);
  apply();
})();
"""


_TABLE_STYLE = """
* { box-sizing: border-box; }
:root {
  --bg:#f5f6f8; --card:#fff; --fg:#1a1d21; --muted:#6b7280; --border:#e5e7eb;
  --green:#15803d; --amber:#b45309; --red:#b91c1c; --accent:#2563eb;
  --row-green:rgba(34,197,94,.10); --row-amber:rgba(245,158,11,.12); --hover:rgba(127,127,127,.10);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg:#0f1216; --card:#171b21; --fg:#e6e8eb; --muted:#9aa4b2; --border:#252b33;
    --green:#4ade80; --amber:#fbbf24; --red:#f87171; --accent:#60a5fa;
    --row-green:rgba(34,197,94,.13); --row-amber:rgba(245,158,11,.13); --hover:rgba(160,160,160,.12);
  }
}
body {
  margin:0; background:var(--bg); color:var(--fg);
  font:13px/1.4 -apple-system, system-ui, "Segoe UI", Roboto, sans-serif;
  -webkit-font-smoothing:antialiased;
  height:100vh; height:100dvh; overflow:hidden;   /* one scroll region: the table */
}
.wrap-wide { max-width:1180px; margin:0 auto; padding:0 12px; height:100%; display:flex; flex-direction:column; }
.topbar {
  flex:0 0 auto; margin:0 -12px 8px; padding:10px 12px;
  background:var(--bg); border-bottom:1px solid var(--border);
}
.summary { font-size:13px; }
.summary .who { color:var(--muted); font-variant-numeric:tabular-nums; }
.stale { color:var(--red); }
.page-link { color:var(--accent); text-decoration:none; font-weight:600; white-space:nowrap; }
.filters { display:flex; flex-direction:column; gap:6px; margin-top:8px; }
.filter-row { display:flex; flex-wrap:wrap; align-items:center; gap:6px; }
.filter-lbl { font-size:11px; color:var(--muted); min-width:42px; }
.chip {
  font:inherit; font-size:12px; line-height:1; padding:5px 11px; border-radius:999px;
  border:1px solid var(--border); background:var(--card); color:var(--fg);
  cursor:pointer; -webkit-appearance:none; appearance:none;
}
.chip.active { background:var(--accent); border-color:var(--accent); color:#fff; }
.no-match { flex:0 0 auto; color:var(--muted); font-size:14px; padding:14px 2px; }
[hidden] { display:none !important; }
.table-scroll { flex:1 1 auto; min-height:0; overflow:auto; }   /* scrolls both axes; header sticks inside */
#grid { width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums; }
#grid th, #grid td { padding:5px 8px; text-align:left; border-bottom:1px solid var(--border); white-space:nowrap; }
#grid thead th {
  position:sticky; top:0; z-index:4; background:var(--bg); cursor:pointer;
  user-select:none; font-weight:600; color:var(--muted); border-bottom:2px solid var(--border);
}
#grid thead th[data-nosort] { cursor:default; }
#grid thead th:hover { color:var(--fg); }
#grid td.spark { padding:2px 8px; }
#grid .spark-svg { width:60px; height:18px; overflow:visible; vertical-align:middle; }
#grid .spark-place polyline { stroke:var(--accent); stroke-width:1.6; fill:none; stroke-linejoin:round; stroke-linecap:round; }
#grid .spark-place circle { fill:var(--accent); }
#grid thead th[aria-sort="ascending"]::after { content:" ▲"; }
#grid thead th[aria-sort="descending"]::after { content:" ▼"; }
#grid td.num { text-align:right; }
#grid td.name { max-width:280px; overflow:hidden; text-overflow:ellipsis; }
#grid .muted, #grid td.muted { color:var(--muted); }
#grid td.upd { color:var(--muted); font-size:12px; }
#grid td.preal .ok { color:var(--green); font-weight:600; }
#grid td.up { color:var(--green); font-weight:600; }
#grid td.down { color:var(--red); font-weight:600; }
#grid tbody tr.pass-real { background:var(--row-green); }
#grid tbody tr.pass-real td:first-child { box-shadow:inset 3px 0 var(--green); }
#grid tbody tr.pass-main { background:var(--row-amber); }
#grid tbody tr.pass-main td:first-child { box-shadow:inset 3px 0 var(--amber); }
#grid tbody tr.absent, #grid tbody tr.nodata { opacity:.6; }
#grid tbody tr:hover { background:var(--hover); }
.foot { flex:0 0 auto; font-size:11px; color:var(--muted); text-align:center; padding:8px 0 4px; }
.empty { color:var(--muted); text-align:center; padding:16px; }
"""


# Click a header to sort (numeric by data-sort, text by text, missing last; the
# Тренд column is data-nosort). ВУЗ/основа chips filter rows (default: show all).
# No JS → static full table.
_TABLE_SCRIPT = """
(function () {
  var table = document.getElementById('grid');
  var noMatch = document.querySelector('.no-match');

  // --- sortable columns ---
  if (table && table.tHead && table.tBodies.length) {
    var tbody = table.tBodies[0];
    var ths = table.tHead.rows[0].cells;
    var val = function (row, idx, num) {
      var td = row.cells[idx];
      if (!td) return null;
      if (num) {
        var d = td.getAttribute('data-sort');
        if (d === null || d === '') return null;
        var n = parseFloat(d);
        return isNaN(n) ? null : n;
      }
      var t = (td.textContent || '').trim().toLowerCase();
      return t === '' || t === '—' ? null : t;
    };
    var sort = function (idx, num, asc) {
      var rows = Array.prototype.slice.call(tbody.rows);
      rows.sort(function (a, b) {
        var va = val(a, idx, num), vb = val(b, idx, num);
        if (va === null && vb === null) return 0;
        if (va === null) return 1;        // missing always last
        if (vb === null) return -1;
        if (va < vb) return asc ? -1 : 1;
        if (va > vb) return asc ? 1 : -1;
        return 0;
      });
      rows.forEach(function (r) { tbody.appendChild(r); });
    };
    Array.prototype.forEach.call(ths, function (th, idx) {
      if (th.hasAttribute('data-nosort')) return;
      var num = th.hasAttribute('data-num');
      th.addEventListener('click', function () {
        var asc = th.getAttribute('aria-sort') !== 'ascending';
        Array.prototype.forEach.call(ths, function (t) { t.removeAttribute('aria-sort'); });
        th.setAttribute('aria-sort', asc ? 'ascending' : 'descending');
        sort(idx, num, asc);
      });
    });
  }

  // --- ВУЗ / основа filter (default: everything visible) ---
  var frows = Array.prototype.slice.call(document.querySelectorAll('.filter-row'));
  var trs = table ? Array.prototype.slice.call(table.tBodies[0].rows) : [];
  if (frows.length && trs.length) {
    var state = { vuz: '__all__', osnova: '__all__' };
    try {
      var saved = JSON.parse(localStorage.getItem('vuz_table_filter') || '{}');
      if (saved.vuz) state.vuz = saved.vuz;
      if (saved.osnova) state.osnova = saved.osnova;
    } catch (e) {}
    var apply = function () {
      var any = false;
      trs.forEach(function (r) {
        var v = r.getAttribute('data-vuz'), o = r.getAttribute('data-osnova');
        var show = (state.vuz === '__all__' || state.vuz === v) &&
                   (state.osnova === '__all__' || state.osnova === o);
        r.hidden = !show; if (show) any = true;
      });
      if (noMatch) noMatch.hidden = any;
      frows.forEach(function (row) {
        var dim = row.getAttribute('data-dim');
        Array.prototype.forEach.call(row.querySelectorAll('.chip'), function (ch) {
          ch.classList.toggle('active', ch.getAttribute('data-val') === state[dim]);
        });
      });
      try { localStorage.setItem('vuz_table_filter', JSON.stringify(state)); } catch (e) {}
    };
    frows.forEach(function (row) {
      var dim = row.getAttribute('data-dim');
      row.addEventListener('click', function (e) {
        var ch = e.target && e.target.closest ? e.target.closest('.chip') : null;
        if (!ch) return;
        state[dim] = ch.getAttribute('data-val');
        apply();
      });
    });
    apply();
  }
})();
"""
