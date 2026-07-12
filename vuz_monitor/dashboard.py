"""Static HTML dashboard, generated from ``state.db`` (Phase 1).

One self-contained page: every ВУЗ + основа (бюджет/платно) as a section, each
specialty as a card with the applicant's current standing and two inline-SVG
sparklines (место — inverted axis, балл). No external CSS/JS/CDN, theme-aware.

Single source of truth is ``state.db``: ``generate(config, store)`` reads the last
snapshot per watch, so ``run`` and the standalone ``dashboard`` CLI produce an
identical page offline. Formatters are shared with the notifier via ``format.py``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .diff import compute_status
from .format import esc, fmt_source_time, g, is_paid, mask_code, pass_real, yesno
from .report import CodeReport, WatchReport, group_reports

MSK = ZoneInfo("Europe/Moscow")
STALE_HOURS = 2  # last snapshot older than this → «данные устарели» hint

_SW, _SH, _SPAD = 120, 28, 3  # sparkline viewBox + padding


# --------------------------------------------------------------------------- #
# Generation from state.db
# --------------------------------------------------------------------------- #
def generate(config, store, now=None) -> str:
    """Build the dashboard HTML from the latest snapshot of each watch."""
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
    return build_html(group_reports(reports), history, now=now)


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


def _card(report, points) -> str:
    name = esc(report.name)
    paid = is_paid(report.title) or is_paid(report.group)

    if report.error:  # defensive; generate() never sets this (reads state.db)
        return (
            f'<div class="card err"><div class="card-main"><span class="spec-name">{name}</span>'
            f'{_pill("нет свежих данных", "muted")}</div>'
            f'<div class="tertiary muted">⚠️ {esc(report.error)}</div></div>'
        )

    st = report.codes[0].status if report.codes else None

    if st is None:  # source never fetched successfully yet
        return (
            f'<div class="card nodata"><div class="card-main"><span class="spec-name">{name}</span>'
            f'{_pill("нет данных", "muted")}</div>'
            f'<div class="tertiary muted">источник ещё не опрашивался</div>'
            f"{_spark_row(points)}</div>"
        )

    if not st.present or st.place is None:  # «выбыл»
        return (
            f'<div class="card absent"><div class="card-main"><span class="spec-name">{name}</span>'
            f'{_pill("выбыл", "muted")}</div>'
            f'<div class="tertiary muted">выбыл из списка</div>'
            f"{_spark_row(points)}</div>"
        )

    # present ----------------------------------------------------------------
    if st.passing_real:
        accent, pill_cls = "pass-real", "green"
    elif st.passing_main:
        accent, pill_cls = "pass-main", "amber"
    else:
        accent, pill_cls = "neutral", "grey"

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
        if st.needs_dormitory is not None:
            consent_txt += f" · Общежитие: {'требуется' if st.needs_dormitory else 'не требуется'}"

    tertiary = f"Основной ВП: {yesno(st.passing_main)} · {esc(consent_txt)}"

    return (
        f'<div class="card {accent}">'
        f'<div class="card-main"><span class="spec-name">{name}</span>'
        f'<span class="place">{esc(place)}</span>{_pill(pass_real(st.passing_real), pill_cls)}</div>'
        f'<div class="secondary">{secondary}</div>'
        f'<div class="tertiary">{tertiary}</div>'
        f"{_spark_row(points)}</div>"
    )


def _group_section(name, reports, history, now) -> str:
    vuz_updated = _updated_label(reports)
    fetched = [r.fetched_at for r in reports if r.fetched_at]
    age = _age_hours(max(fetched), now) if fetched else None
    stale = ""
    if age is not None and age > STALE_HOURS:
        stale = f'<span class="stale">⚠️ данные от {esc(_fetched_msk(max(fetched)))}</span>'

    # count where the applicant currently passes (Проходной ВП) in this group
    passing = sum(
        1 for r in reports for cr in r.codes
        if cr.status and cr.status.present and cr.status.passing_real
    )
    cards = []
    for r in reports:
        disp = r.codes[0].status.code_display if (r.codes and r.codes[0].status) else None
        pts = history.get((r.watch_id, disp), []) if disp is not None else []
        cards.append(_card(r, pts))

    return (
        '<section class="group">'
        f'<div class="group-header"><span class="group-title">{esc(name)}</span>'
        f'<span class="group-meta">проходите: {passing}/{len(reports)} · обновлено {esc(vuz_updated)}{stale}</span></div>'
        + "".join(cards)
        + "</section>"
    )


def build_html(groups, history, now=None) -> str:
    """Render the full page. `groups` = group_reports() output; `history` =
    {(watch_id, code_display): [daily points]}."""
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    flat = [r for _, reps in groups for r in reps]
    all_codes = [cr for r in flat for cr in r.codes]
    present = [cr.status for cr in all_codes if cr.status and cr.status.present]
    n_total = len(all_codes)
    n_real = sum(1 for s in present if s.passing_real)
    n_main = sum(1 for s in present if s.passing_main)
    n_consent = sum(1 for s in present if s.consent)
    updated = _updated_label(flat)

    codes = []
    for cr in all_codes:
        if cr.status is not None and cr.status.code_display not in codes:
            codes.append(cr.status.code_display)
    who = " / ".join(mask_code(c) for c in codes)

    fetched = [r.fetched_at for r in flat if r.fetched_at]
    age = _age_hours(max(fetched), now) if fetched else None
    summary_stale = ""
    if age is not None and age > STALE_HOURS:
        summary_stale = f' · <span class="stale">данные устарели ({int(age)} ч)</span>'

    sections = "".join(_group_section(name, reps, history, now) for name, reps in groups) or (
        '<p class="empty">Нет отслеживаемых списков.</p>'
    )

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
        '<div class="summary">'
        + (f'<span class="who">{esc(who)}</span> · ' if who else "")
        + f'<b>Проходной ВП: {n_real}/{n_total}</b> · Основной ВП: {n_main} · '
        f'согласий: {n_consent} · обновлено {esc(updated)}{summary_stale}'
        "</div>\n"
        f"{sections}\n"
        '<footer class="foot">обновляется каждый час · vuz_monitor</footer>\n'
        "</div>\n"
        "</body></html>\n"
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
.summary {
  position:sticky; top:0; z-index:3; margin:0 -12px 12px; padding:10px 12px;
  background:var(--bg); border-bottom:1px solid var(--border); font-size:13px;
}
.summary .who { color:var(--muted); font-variant-numeric:tabular-nums; }
.stale { color:var(--red); }
.group { margin-bottom:20px; }
.group-header {
  position:sticky; top:41px; z-index:2; display:flex; flex-wrap:wrap;
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
.card-main { display:flex; flex-wrap:wrap; align-items:baseline; gap:6px 8px; }
.spec-name { font-size:15px; font-weight:600; flex:1 1 auto; }
.place { font-size:13px; color:var(--muted); }
.pill {
  font-size:12px; font-weight:600; padding:2px 8px; border-radius:999px; white-space:nowrap;
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
