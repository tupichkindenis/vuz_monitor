"""Shared display formatters used by both the Telegram notifier and the dashboard.

Pure functions, no side effects. Kept in one place so notify and dashboard render
identical values (DRY).
"""
from __future__ import annotations

import html
from datetime import datetime


def esc(s) -> str:
    """HTML-escape any interpolated value (name, code, field). Never trust input."""
    return html.escape(str(s)) if s is not None else ""


def g(v) -> str:
    """Compact number: 302.0 -> '302', 292.5 -> '292.5', None -> '—'."""
    if v is None:
        return "—"
    return f"{v:g}"


def mask_code(code) -> str:
    """'1366129' -> '•••6129'. Masks all but the last 4 digits for the public page
    (harder to tie the номер to a person via the URL). Short codes returned as-is."""
    s = str(code or "")
    return "•••" + s[-4:] if len(s) > 4 else s


def yesno(v) -> str:
    if v is None:
        return "нет данных"
    return "да" if v else "нет"


def pass_real(v) -> str:
    if v is None:
        return "нет данных"
    return "проходите" if v else "не проходите"


def split_group(name: str):
    """'МИРЭА — бюджет' -> ('МИРЭА', 'бюджет'). Returns (None, None) if no separator."""
    for sep in (" — ", " – ", " - "):
        if sep in name:
            vuz, konkurs = name.split(sep, 1)
            return vuz.strip(), konkurs.strip()
    return None, None


def is_paid(title) -> bool:
    t = (title or "").lower()
    return "договор" in t or "платн" in t


def fmt_source_time(s) -> str:
    """Source 'YYYY-MM-DD HH:MM:SS' -> 'DD.MM HH:MM'. '—' when missing/unparseable."""
    if not s:
        return "—"
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").strftime("%d.%m %H:%M")
    except (ValueError, TypeError):
        return str(s)
