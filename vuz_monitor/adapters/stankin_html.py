"""Станкин (priem.stankin.ru, Bitrix) ranked-list adapter.

The public ranked-lists page is a JS filter UI, but applying a filter does a plain GET to
`/gridspisokpostupayushchikh/` with the filter params — a SERVER-RENDERED HTML table. Filtering
by `PROPERTY_423=<код>` returns just the applicant's row with their true rank (№). So a watch's
`url` is that grid endpoint and `params` carries the filter (основа, форма, направление, код,
apply_filter=Y, LIST_TYPE=ranked, EDU_LEVEL=bs, …).

Columns are mapped by header LABEL (robust to budget/paid differences). Single-level header, an
explicit № column (= rank), plain-integer scores, «да»/empty flags (empty = «нет»).
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from ..config import WatchConfig
from ..models import Entrant, ProgramMeta, Snapshot, normalize_code
from .base import Adapter, now_iso, to_int, to_num, truthy

# field -> header keyword (lowercased substring). Order matters: more specific first so
# "Приоритет" binds before "Высший проходной приоритет"/"Основной высший приоритет".
_FIELD_KEYWORDS = [
    ("code", "уникальный код"),
    ("priority", "приоритет"),
    ("passing_real", "высший проходной"),
    ("passing_main", "основной высший"),
    ("consent", "согласие"),
    ("dormitory", "общежит"),
    ("contract", "договор"),
    ("payment", "оплата"),
    ("final_score", "сумма баллов с ид"),
]


def _rows(table):
    return [
        [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        for tr in table.find_all("tr")
    ]


def _is_header(row) -> bool:
    return any("уникальный код" in c.lower() for c in row)


class StankinHtmlAdapter(Adapter):
    def fetch(self, watch: WatchConfig) -> Snapshot:
        resp = self._get(watch.url, params=watch.params)
        html = resp.content.decode(watch.encoding or resp.encoding or "utf-8", errors="replace")
        return self.parse(html, watch)

    def parse(self, html: str, watch: WatchConfig) -> Snapshot:
        soup = BeautifulSoup(html, "lxml")
        table, header = self._find_table(soup)
        if table is None:
            raise ValueError(f"ranked table not found at {watch.url}")

        cols = self._build_colmap(header)
        code_idx = cols.get("code", 0)
        # A «договор» column («Наличие договора …») marks a paid list. Its condition is
        # having the contract (and payment too, if that column exists — МЭИ-style).
        paid = "contract" in cols

        def cell(row, key):
            i = cols.get(key)
            return row[i] if i is not None and i < len(row) else None

        entrants = []
        for row in _rows(table):
            if _is_header(row) or code_idx >= len(row):
                continue
            code = normalize_code(row[code_idx])
            if len(code) < 5:  # skip header / non-applicant rows
                continue
            if paid:
                contract = truthy(cell(row, "contract"))
                payment = truthy(cell(row, "payment")) if "payment" in cols else None
                consent = bool(contract and (payment is None or payment))
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
                    passing_main=truthy(cell(row, "passing_main")),
                    passing_real=truthy(cell(row, "passing_real")),
                    needs_dormitory=(
                        truthy(cell(row, "dormitory")) if "dormitory" in cols else None
                    ),
                    raw={"cells": row},
                )
            )

        meta = ProgramMeta(title=watch.name, plan=None, total=None, updated_at=None)
        return Snapshot(
            watch_id=watch.watch_id, meta=meta, entrants=entrants, fetched_at=now_iso()
        )

    def _find_table(self, soup):
        for t in soup.find_all("table"):
            for row in _rows(t):
                if _is_header(row):
                    return t, row
        return None, None

    @staticmethod
    def _build_colmap(header) -> dict:
        cols = {}
        for idx, raw in enumerate(header):
            c = raw.strip().lower()
            if "place" not in cols and c.startswith("№"):
                cols["place"] = idx
            for field, kw in _FIELD_KEYWORDS:
                if field not in cols and kw in c:
                    cols[field] = idx
        return cols
