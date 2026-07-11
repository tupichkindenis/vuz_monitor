"""Generic HTML-table adapter for ВУЗ sites that publish lists as a <table>.

Configure per watch:
    table_selector: CSS selector for the <table> (omit -> first <table>)
    columns:  { code: <idx>, place: <idx>, final_score: <idx>, consent: <idx>, priority: <idx> }
    encoding: e.g. "windows-1251" (omit -> response/utf-8)
    plan_override: budget places (HTML lists rarely publish it)
Column values are 0-based cell indexes. `place` is optional — if absent, rank is
inferred from row order.
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from ..config import WatchConfig
from ..models import Entrant, ProgramMeta, Snapshot, normalize_code
from .base import Adapter, now_iso, to_int, to_num, truthy


class HtmlTableAdapter(Adapter):
    def fetch(self, watch: WatchConfig) -> Snapshot:
        resp = self._get(watch.url)
        enc = watch.encoding or resp.encoding or "utf-8"
        html_text = resp.content.decode(enc, errors="replace")
        return self.parse(html_text, watch)

    def parse(self, html_text: str, watch: WatchConfig) -> Snapshot:
        soup = BeautifulSoup(html_text, "lxml")
        table = (
            soup.select_one(watch.table_selector)
            if watch.table_selector
            else soup.find("table")
        )
        if table is None:
            raise ValueError(
                f"table not found (selector={watch.table_selector!r}) at {watch.url}"
            )

        cols = watch.columns or {}
        entrants = []
        order = 0
        for tr in table.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if not cells:
                continue  # header row (<th>) or spacer

            def cell(key):
                idx = cols.get(key)
                if idx is None or idx >= len(cells):
                    return None
                return cells[idx]

            code_display = cell("code")
            if not code_display:
                continue

            order += 1
            place = to_int(cell("place"))
            if place is None:
                place = order

            entrants.append(
                Entrant(
                    code=normalize_code(code_display),
                    code_display=code_display,
                    place=place,
                    final_score=to_num(cell("final_score")),
                    priority=to_int(cell("priority")),
                    consent=truthy(cell("consent")),
                    # Optional columns — map them if the site publishes ВП / платное.
                    passing_main=truthy(cell("passing_main")) if "passing_main" in cols else None,
                    passing_real=truthy(cell("passing_real")) if "passing_real" in cols else None,
                    paid_ok=truthy(cell("paid_ok")) if "paid_ok" in cols else None,
                    needs_dormitory=truthy(cell("needs_dormitory")) if "needs_dormitory" in cols else None,
                    raw={"cells": cells},
                )
            )

        meta = ProgramMeta(
            title=watch.name,
            plan=watch.plan_override,
            total=len(entrants),
            updated_at=None,  # HTML sources rarely expose one; diff still works
        )
        return Snapshot(
            watch_id=watch.watch_id,
            meta=meta,
            entrants=entrants,
            fetched_at=now_iso(),
        )
