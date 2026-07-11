"""МЭИ (pk.mpei.ru) HTML list adapter.

МЭИ publishes each specialty as an HTML page with ONE `<table>` that has a two-level
colspan header and no № column (rank = row order). Program metadata lives in the page
text above the table, not in the table:
    «Количество вакантных мест: 201» and «данные на 11:35 11.07.2026».

Two page variants, auto-detected by the header (presence of an «Оплата» column):
- budget: … Согласие · Приоритет · Основной высший · Высший проходной · Общежитие
- paid:   … Договор · Оплата · Приоритет · Основной высший · Высший проходной · Общежитие
Scores are plain integers (259), NOT ×1000. Flags are «да»/«нет».
"""
from __future__ import annotations

import re
from datetime import datetime

from bs4 import BeautifulSoup

from ..config import WatchConfig
from ..models import Entrant, ProgramMeta, Snapshot, normalize_code
from .base import Adapter, now_iso, to_int, to_num, truthy

_PLAN_RE = re.compile(r"вакантных мест[:\s]*(\d+)", re.IGNORECASE)
_UPDATED_RE = re.compile(r"данные на\s*(\d{1,2}:\d{2}\s+\d{2}\.\d{2}\.\d{4})")

# Leaf-column indices in DATA rows (verified against pk.mpei.ru).
_COLS_BUDGET = {
    "code": 0, "final_score": 1, "consent": 9, "priority": 10,
    "passing_main": 11, "passing_real": 12, "dormitory": 13,
}
_COLS_PAID = {
    "code": 0, "final_score": 1, "contract": 9, "payment": 10, "priority": 11,
    "passing_main": 12, "passing_real": 13, "dormitory": 14,
}


def _iso(raw: str) -> "str | None":
    """'11:35 11.07.2026' -> '2026-07-11 11:35:00' (uniform with the МИРЭА adapter)."""
    try:
        return datetime.strptime(raw.strip(), "%H:%M %d.%m.%Y").strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return raw or None


def _dorm(value: str) -> "bool | None":
    v = (value or "").strip().lower()
    if v.startswith("с/о"):
        return True
    if v.startswith("б/о"):
        return False
    return None


class MpeiHtmlAdapter(Adapter):
    def fetch(self, watch: WatchConfig) -> Snapshot:
        resp = self._get(watch.url)
        html = resp.content.decode(watch.encoding or resp.encoding or "utf-8", errors="replace")
        return self.parse(html, watch)

    def parse(self, html: str, watch: WatchConfig) -> Snapshot:
        soup = BeautifulSoup(html, "lxml")
        table = soup.find("table")
        if table is None:
            raise ValueError(f"table not found at {watch.url}")

        plan_m = _PLAN_RE.search(html)
        plan = int(plan_m.group(1)) if plan_m else None
        upd_m = _UPDATED_RE.search(html)
        updated_at = _iso(upd_m.group(1)) if upd_m else None

        rows = [
            [td.get_text(strip=True) for td in tr.find_all("td")]
            for tr in table.find_all("tr")
        ]
        rows = [c for c in rows if c]  # drop rows with no <td>

        paid = any("оплата" in c.lower() for row in rows[:3] for c in row)
        cols = _COLS_PAID if paid else _COLS_BUDGET

        def cell(row, key):
            idx = cols.get(key)
            return row[idx] if idx is not None and idx < len(row) else None

        entrants = []
        place = 0
        for row in rows:
            code = normalize_code(row[0]) if row else ""
            if len(code) < 5:  # header rows (colspan / sub-header) have a text cell[0]
                continue
            place += 1
            if paid:
                contract = truthy(cell(row, "contract"))
                payment = truthy(cell(row, "payment"))
                consent = contract and payment
            else:
                contract = payment = None
                consent = truthy(cell(row, "consent"))
            entrants.append(
                Entrant(
                    code=code,
                    code_display=row[0].strip(),
                    place=place,
                    final_score=to_num(cell(row, "final_score")),
                    priority=to_int(cell(row, "priority")),
                    consent=consent,
                    contract=contract,
                    payment=payment,
                    passing_main=truthy(cell(row, "passing_main")),
                    passing_real=truthy(cell(row, "passing_real")),
                    needs_dormitory=_dorm(cell(row, "dormitory")),
                    raw={"cells": row},
                )
            )

        meta = ProgramMeta(
            title=watch.name,
            plan=plan,
            total=len(entrants),
            updated_at=updated_at,
        )
        return Snapshot(
            watch_id=watch.watch_id,
            meta=meta,
            entrants=entrants,
            fetched_at=now_iso(),
        )
