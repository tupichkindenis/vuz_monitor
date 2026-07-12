"""Message building + Telegram Bot API delivery (raw HTTPS, no extra dependency)."""
from __future__ import annotations

import httpx

from .format import (
    esc as _esc,
    fmt_source_time as _fmt_source_time,
    g as _g,
    is_paid as _is_paid,
    pass_real as _pass_real,
    split_group as _split_group,
    yesno as _yesno,
)

TG_LIMIT = 4096
API = "https://api.telegram.org/bot{token}/{method}"


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #
def _specialty_block(report, show_code: bool) -> str:
    """One specialty (list) inside a group message: bold name + a bulleted standing."""
    head = f"📋 <b>{_esc(report.name)}</b>"
    if report.error:
        return f"{head}\n– ⚠️ ошибка: {_esc(report.error)}"

    paid = _is_paid(report.title) or _is_paid(report.group)
    lines = [head]
    for cr in report.codes:
        st = cr.status
        if show_code:
            lines.append(f"👤 <b>{_esc(st.code_display)}</b>")
        if not st.present or st.place is None:
            lines.append("– ❌ не найден в списке")
        else:
            lines.append(f"– балл: {_g(st.final_score)}")
            # total / plan may be unknown (e.g. Станкин) — omit those parts then.
            place_line = f"– место: {st.place}{_place_delta(cr)}"
            if st.total is not None:
                place_line += f" из {st.total}"
            if st.plan is not None:
                place_line += f" (всего {st.plan} мест)"
            lines.append(place_line)
            if paid:
                # ВП-флаги важны и на платном (сигнал прохождения) — показываем их тоже.
                # «Соблюдены условия для платного» = accepted / наличие договора (у МЭИ
                # = договор И оплата), с расшифровкой.
                lines.append(
                    f"– ВП прох./основ.: {_pass_real(st.passing_real)} · {_yesno(st.passing_main)}"
                )
                line = f"– Соблюдены условия для платного: {_yesno(st.consent)}"
                detail = []
                if st.contract is not None:
                    detail.append(f"договор: {_yesno(st.contract)}")
                if st.payment is not None:
                    detail.append(f"оплата: {_yesno(st.payment)}")
                if detail:
                    line += f" ({', '.join(detail)})"
                lines.append(line)
            else:
                lines.append(
                    f"– ВП прох./основ.: {_pass_real(st.passing_real)} · {_yesno(st.passing_main)}"
                )
                lines.append(f"– Согласие: {_yesno(st.consent)}")
        # place shown inline; paid_ok (МИРЭА pc) unused. ВП/consent tracked on both.
        lines += _change_lines(cr, exclude={"place", "paid_ok"}, is_paid=paid)
    return "\n".join(lines)


def _change_text(ch, is_paid: bool = False) -> str:
    f = ch.field
    if f == "place":
        moved_up = ch.new is not None and ch.old is not None and ch.new < ch.old
        arrow = "▲ вверх" if moved_up else "▼ вниз"
        return f"• место: {ch.old} → {ch.new} ({arrow})"
    if f == "final_score":
        return f"• балл: {_g(ch.old)} → {_g(ch.new)}"
    if f == "priority":
        return f"• приоритет: {ch.old} → {ch.new}"
    if f == "consent":
        label = "Соблюдены условия для платного" if is_paid else "согласие"
        return f"• {label}: {_yesno(ch.old)} → {_yesno(ch.new)}"
    if f == "contract":
        return f"• договор: {_yesno(ch.old)} → {_yesno(ch.new)}"
    if f == "payment":
        return f"• оплата: {_yesno(ch.old)} → {_yesno(ch.new)}"
    if f == "passing_real":
        return f"• Проходной ВП: {_pass_real(ch.old)} → {_pass_real(ch.new)}"
    if f == "passing_main":
        return f"• Основной ВП: {_yesno(ch.old)} → {_yesno(ch.new)}"
    if f == "present":
        return "• появились в списке" if ch.new else "• пропали из списка"
    return f"• {_esc(f)}: {_esc(ch.old)} → {_esc(ch.new)}"


def _change_lines(cr, exclude=(), is_paid: bool = False) -> list:
    if cr.first_run:
        return ["• первый запуск — отслеживаю с этого момента"]
    return [_change_text(ch, is_paid) for ch in cr.changes if ch.field not in exclude]


def _place_delta(cr) -> str:
    """Inline movement vs the previous update: (▲ N) up, (▼ N) down, '' if unchanged."""
    for ch in cr.changes:
        if ch.field == "place" and ch.old is not None and ch.new is not None:
            if ch.new < ch.old:            # smaller place = higher rank
                return f" (▲ {ch.old - ch.new})"
            if ch.new > ch.old:
                return f" (▼ {ch.new - ch.old})"
    return ""


def _group_updated_at(reports):
    """Most recent source updatedAt across the group's lists."""
    times = [r.meta.updated_at for r in reports if r.meta and r.meta.updated_at]
    return max(times) if times else None


def _distinct_codes(reports: list) -> list:
    codes = []
    for r in reports:
        for cr in r.codes:
            cd = cr.status.code_display
            if cd not in codes:
                codes.append(cd)
    return codes


def _pack(header: str, blocks: list) -> list:
    """Header + blocks into <=TG_LIMIT messages, repeating header on overflow."""
    full = header + ("\n\n" + "\n\n".join(blocks) if blocks else "")
    if len(full) <= TG_LIMIT:
        return [full]
    messages = []
    cur = header
    for b in blocks:
        candidate = f"{cur}\n\n{b}"
        if len(candidate) > TG_LIMIT and cur != header:
            messages.append(cur)
            cur = f"{header}\n\n{b}"
        else:
            cur = candidate
    messages.append(cur)
    return messages


def _group_message(group_name: str, reports: list) -> list:
    """One Telegram message per group (ВУЗ + конкурс), all its specialties inside."""
    codes = _distinct_codes(reports)
    single = len(codes) == 1
    vuz, konkurs = _split_group(group_name)
    upd_raw = _group_updated_at(reports)
    upd = f" · обновлено {_fmt_source_time(upd_raw)}" if upd_raw else ""

    if single and vuz and konkurs:
        header = f"🎓 {_esc(vuz)} — <b>{_esc(codes[0])}</b> · {_esc(konkurs)}{upd}"
    elif single:
        header = f"🎓 {_esc(group_name)} — <b>{_esc(codes[0])}</b>{upd}"
    else:
        header = f"🎓 <b>{_esc(group_name)}</b>{upd}"

    blocks = [_specialty_block(r, show_code=not single) for r in reports]
    return _pack(header, blocks)


def build_messages(groups: list) -> list:
    """Render one message per group. `groups` is a list of (name, [WatchReport])."""
    out = []
    for name, reports in groups:
        out.extend(_group_message(name, reports))
    return out


# --------------------------------------------------------------------------- #
# Telegram API
# --------------------------------------------------------------------------- #
def _api_call(token: str, method: str, json=None) -> dict:
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set (env or .env).")
    url = API.format(token=token, method=method)
    try:
        resp = httpx.post(url, json=json, timeout=30) if json is not None else httpx.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        # Never surface the URL — it contains the bot token — in logs/exceptions.
        raise RuntimeError(f"Telegram {method} failed: HTTP {exc.response.status_code}") from None
    except httpx.HTTPError:
        raise RuntimeError(f"Telegram {method}: network error") from None


def send_message(token: str, chat_id: str, text: str) -> dict:
    if not chat_id:
        raise RuntimeError("telegram.chat_id is not set in config.yaml.")
    return _api_call(
        token,
        "sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
    )


def get_updates(token: str) -> dict:
    return _api_call(token, "getUpdates")
