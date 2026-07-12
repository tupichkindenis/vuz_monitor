"""Adapter base class + shared parsing helpers.

An adapter turns one data source into a normalized :class:`Snapshot`. Add a ВУЗ by
writing one adapter (or reusing ``html_table`` with a column map).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone

import httpx

from ..config import WatchConfig
from ..models import Snapshot, normalize_code

DEFAULT_HEADERS = {
    "User-Agent": "vuz_monitor/0.1 (personal admission-list monitor)",
    "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
}


class Adapter(ABC):
    timeout = 30.0

    @abstractmethod
    def fetch(self, watch: WatchConfig) -> Snapshot:
        """Fetch the source and return a normalized snapshot. May raise on failure."""

    def _get(self, url: str, params=None, headers=None) -> httpx.Response:
        h = dict(DEFAULT_HEADERS)
        h.update(headers or {})
        resp = httpx.get(
            url, params=params, headers=h, timeout=self.timeout, follow_redirects=True
        )
        resp.raise_for_status()
        return resp


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_int(v) -> "int | None":
    try:
        if v is None or v == "":
            return None
        return int(str(v).strip())
    except (ValueError, TypeError):
        return None


def to_num(v) -> "float | None":
    if v is None or v == "":
        return None
    try:
        return float(str(v).strip().replace(",", "."))
    except (ValueError, TypeError):
        return None


def to_score(v, scale: float = 1000.0) -> "float | None":
    """MIREA scores are integers scaled ×1000 (302000 -> 302.0)."""
    n = to_num(v)
    return None if n is None else n / scale


def truthy(v) -> bool:
    """Interpret an HTML cell / flag as a boolean consent marker."""
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in {"1", "да", "+", "yes", "true", "оригинал", "согласие", "есть", "✓", "v"}


# --------------------------------------------------------------------------- #
# Shared HTML-table parsing (label→index) — used by stankin_html / mai_html.
# Columns are matched by HEADER LABEL, so budget/paid column differences don't
# break the mapping. colspan is expanded so header and data indices stay aligned.
# --------------------------------------------------------------------------- #
def _clean(s) -> str:
    """Normalize a header/label cell for matching: NBSP → space, collapse
    whitespace, lowercase. Keeps header matching robust to МАИ-style «Уникальный\xa0код»."""
    return " ".join(str(s or "").replace("\xa0", " ").split()).lower()


def table_rows(table) -> list:
    """Table as a list of rows (each a list of cell texts), expanding colspan."""
    out = []
    for tr in table.find_all("tr"):
        row = []
        for cell in tr.find_all(["td", "th"]):
            span = to_int(cell.get("colspan")) or 1
            row.extend([cell.get_text(strip=True)] * span)
        out.append(row)
    return out


def has_code_header(row, key: str = "уникальный код") -> bool:
    return any(key in _clean(c) for c in row)


def build_colmap(header: list, field_keywords: list) -> dict:
    """``header`` cells + ``[(field, keyword)]`` -> ``{field: column_index}``.
    ``'№'`` binds to ``place``; first column matching a keyword (lowercased
    substring) wins per field, so order keywords specific-first."""
    cols = {}
    for idx, raw in enumerate(header):
        c = _clean(raw)
        if "place" not in cols and c.startswith("№"):
            cols["place"] = idx
        for field, kw in field_keywords:
            if field not in cols and kw in c:
                cols[field] = idx
    return cols


def find_data_table(soup, key: str = "уникальный код", pick: str = "first"):
    """Find the ``<table>`` whose header row contains ``key``.

    ``pick='first'`` returns the first such table; ``pick='largest'`` returns the
    one with the most data rows (robust when the page has small quota tables plus
    a big main competition table, and the main table has no distinctive title).
    Returns ``(table, header_row)`` or ``(None, None)``.
    """
    best = None
    for t in soup.find_all("table"):
        header = next((r for r in table_rows(t) if has_code_header(r, key)), None)
        if header is None:
            continue
        if pick == "first":
            return t, header
        n = sum(1 for tr in t.find_all("tr") if tr.find_all("td"))
        if best is None or n > best[2]:
            best = (t, header, n)
    return (best[0], best[1]) if best else (None, None)


def parse_labeled_table(table, field_keywords: list, code_key: str = "уникальный код"):
    """``(table, [(field, keyword)])`` -> ``(colmap, data_rows)``.

    ``colmap`` is ``{field: index}``; ``data_rows`` are applicant rows (the code
    column normalizes to >=5 digits), colspan-expanded. Skips header/service rows.
    """
    rows = table_rows(table)
    header = next((r for r in rows if has_code_header(r, code_key)), rows[0] if rows else [])
    cols = build_colmap(header, field_keywords)
    ci = cols.get("code", 0)
    data = [r for r in rows if ci < len(r) and len(normalize_code(r[ci])) >= 5]
    return cols, data
