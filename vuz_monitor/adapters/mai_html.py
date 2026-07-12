"""МАИ (priem.mai.ru) ranked-list adapter.

The filter UI is a cascade of STATIC HTML files: each `<select>` change loads
`https://public.mai.ru/priem/list/data/{token}.html` (jQuery `.load()`), and the
terminal file holds the ranked-list table. The tokens embed the snapshot
timestamp and are regenerated on every data update, so the adapter walks the
cascade BY LABEL each run (place → level → направление → форма → основа) instead
of storing a fixed URL. Watch `params` carries the labels:

    params: {place: "МАИ", level: "Базовое высшее образование",
             spec: "Информатика и вычислительная техника",
             form: "Очная", pay: "Бюджет"}   # or pay: "Платная"

The terminal page has several tables (quotas + the main competition list); we
take the LARGEST table with a «Уникальный код» header. Columns are mapped by
label, so budget («Согласие») and paid («Договор», no «Оплата») share one
adapter. МАИ publishes no ВП flags and no КЦП (passing_*/plan = None) but DOES
publish «Дата последнего обновления» → updated_at.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..config import WatchConfig
from ..models import Entrant, ProgramMeta, Snapshot, normalize_code
from .base import (
    Adapter,
    find_data_table,
    now_iso,
    parse_labeled_table,
    to_int,
    to_num,
    truthy,
)

DATA_URL = "https://public.mai.ru/priem/list/data/{}.html"
_NBSP = " "
_DASHES = "‐‑‒–—−"  # unicode hyphens/minus → '-'
_UPDATED_RE = re.compile(
    r"Дата последнего обновления:\s*(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}:\d{2}:\d{2})"
)

# field -> header keyword (lowercased substring). "сумма баллов" binds to the
# plain «Сумма баллов» column (first match), not «… по предметам» / «… за ИД».
_FIELD_KEYWORDS = [
    ("code", "уникальный код"),
    ("final_score", "сумма баллов"),
    ("priority", "приоритет"),
    ("consent", "согласие"),
    ("contract", "договор"),
    ("dormitory", "общежит"),
]


def _norm(s: str) -> str:
    s = (s or "").replace(_NBSP, " ")
    for d in _DASHES:
        s = s.replace(d, "-")
    return " ".join(s.split()).strip().lower()


def _parse_updated(html: str):
    """«Дата последнего обновления: 12.07.2026 15:58:18» -> «2026-07-12 15:58:18».

    Normalized to the internal YYYY-MM-DD HH:MM:SS so fmt_source_time and the
    on_change_only comparison both work. None if the stamp is absent."""
    m = _UPDATED_RE.search(html or "")
    if not m:
        return None
    d, mo, y, t = m.groups()
    return f"{y}-{mo}-{d} {t}"


class MaiHtmlAdapter(Adapter):
    def fetch(self, watch: WatchConfig) -> Snapshot:
        p = watch.params or {}
        for key in ("place", "level", "spec", "form", "pay"):
            if not p.get(key):
                raise ValueError(f"mai_html watch {watch.name!r}: missing params.{key}")

        # Walk the cascade by label. The root <select id="place"> lives on the
        # list page; every deeper step is an <option> fragment under /data/.
        root = self._get(watch.url).text
        token = self._pick(root, p["place"], select_id="place", step="place")
        for step in ("level", "spec", "form", "pay"):
            token = self._pick(self._data(token), p[step], step=step)

        return self.parse(self._data(token), watch)

    def _data(self, token: str) -> str:
        resp = self._get(self._data_url(token))
        return resp.content.decode(resp.encoding or "utf-8", errors="replace")

    @staticmethod
    def _data_url(value: str) -> str:
        """Build the /data/ URL defensively — the option value is normally a bare
        token, but tolerate a full URL / absolute path just in case."""
        v = (value or "").strip()
        if v.startswith("http://") or v.startswith("https://"):
            return v
        if v.startswith("/"):
            return "https://public.mai.ru" + v
        return DATA_URL.format(v)

    @staticmethod
    def _pick(html: str, label: str, select_id: str = None, step: str = "") -> str:
        """Return the <option> value whose text matches `label` exactly (after
        normalization). Requires EXACTLY ONE match — 0 or >1 is a clear error."""
        soup = BeautifulSoup(html, "lxml")
        scope = soup.find("select", id=select_id) if select_id else soup
        if scope is None:
            raise ValueError(f"mai_html: <select id={select_id!r}> not found (step {step})")
        want = _norm(label)
        matches = [
            (o.get("value") or "").strip()
            for o in scope.find_all("option")
            if _norm(o.get_text()) == want and (o.get("value") or "").strip() not in ("", "0")
        ]
        if len(matches) != 1:
            raise ValueError(
                f"mai_html: step {step!r} expected exactly 1 option «{label}», got {len(matches)}"
            )
        return matches[0]

    def parse(self, html: str, watch: WatchConfig) -> Snapshot:
        soup = BeautifulSoup(html, "lxml")
        table, _ = find_data_table(soup, pick="largest")
        if table is None:
            raise ValueError(f"mai_html: ranked table not found for {watch.name!r}")

        cols, rows = parse_labeled_table(table, _FIELD_KEYWORDS)
        code_idx = cols.get("code", 0)
        paid = "contract" in cols  # «Договор» column = paid list

        def cell(row, key):
            i = cols.get(key)
            return row[i] if i is not None and i < len(row) else None

        entrants = []
        for row in rows:
            code = normalize_code(row[code_idx])
            if paid:
                # МАИ paid has «Договор» but no «Оплата» → condition is the contract.
                contract = truthy(cell(row, "contract"))
                consent, payment = contract, None
            else:
                contract = payment = None
                consent = truthy(cell(row, "consent"))
            entrants.append(
                Entrant(
                    code=code,
                    code_display=row[code_idx].strip(),
                    place=to_int(cell(row, "place")),
                    final_score=to_num(cell(row, "final_score")),
                    priority=to_int(cell(row, "priority")),
                    consent=consent,
                    contract=contract,
                    payment=payment,
                    passing_main=None,  # МАИ publishes no ВП flags
                    passing_real=None,
                    needs_dormitory=(
                        truthy(cell(row, "dormitory")) if "dormitory" in cols else None
                    ),
                    raw={},  # state.db is already large; МАИ lists are 1-3k rows
                )
            )

        meta = ProgramMeta(
            title=watch.name,
            plan=None,
            total=len(entrants),
            updated_at=_parse_updated(html),
        )
        return Snapshot(
            watch_id=watch.watch_id, meta=meta, entrants=entrants, fetched_at=now_iso()
        )
