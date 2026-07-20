"""MIREA-style JSON REST adapter (reference adapter).

Endpoint shape (verified against priem.mirea.ru):
    { "data": [ {                       # one object per requested competition
        "id": "...", "title": "...", "programSetTitle": "...",
        "plan": 122, "appCount": 1993, "minScore": 81,
        "updatedAt": "2026-07-10 15:17:40", "isFinal": 0,
        "entrants": [ {snils, place, finalMark, entranceMark, achievementMark,
                       priority, accepted, isBVI, isActive, ...} ]
      } ] }
Scores are integers ×1000 (302000 -> 302.0). Confirmed field meanings:
`accepted` = согласие на зачисление, `iHP` = Основной ВП (guaranteed if you consent;
count == plan), `iHPO` = Проходной ВП (would be admitted per current consents),
`pc` = «Соблюдены условия для платного» (contract signed + paid).
A watch requests one competition (`competitions[]=<id>`), so we read `data[0]`.
Applicants are matched by код участника (`superCode`) by default; override per watch
with `code_field:` ("snils" or "id"). There is no ИНН field in this API.
"""
from __future__ import annotations

from ..config import WatchConfig
from ..models import Entrant, ProgramMeta, Snapshot, normalize_code
from .base import Adapter, now_iso, to_int, to_num, to_score


class MireaApiAdapter(Adapter):
    def fetch(self, watch: WatchConfig) -> Snapshot:
        resp = self._get(watch.url, params=watch.params)
        return self.parse(resp.json(), watch)

    @staticmethod
    def _competition(data) -> dict:
        """Return the competition object holding meta + entrants."""
        block = data.get("data") if isinstance(data, dict) else data
        if isinstance(block, list) and block:
            return block[0]
        if isinstance(block, dict):
            return block
        return data if isinstance(data, dict) else {}

    def parse(self, data, watch: WatchConfig) -> Snapshot:
        comp = self._competition(data)
        rows = comp.get("entrants") or comp.get("data") or []

        # Match applicants by код участника (superCode) unless the watch overrides it.
        code_field = watch.code_field or "superCode"

        entrants = []
        for row in rows:
            raw_code = row.get(code_field)
            if raw_code is None:  # fall back so a missing field never blanks the code
                raw_code = row.get("superCode") or row.get("snils") or row.get("id")
            code_display = str(raw_code or "")
            entrants.append(
                Entrant(
                    code=normalize_code(code_display),
                    code_display=code_display,
                    place=to_int(row.get("place")),
                    final_score=to_score(row.get("finalMark")),
                    entrance_score=to_score(row.get("entranceMark")),
                    achievement_score=to_score(row.get("achievementMark")),
                    priority=to_int(row.get("priority")),
                    consent=bool(row.get("accepted")),          # согласие на зачисление
                    passing_main=bool(row.get("iHP")),          # Основной ВП
                    passing_real=bool(row.get("iHPO")),         # Проходной ВП
                    paid_ok=bool(row.get("pc")),                # условия для платного
                    needs_dormitory=bool(row.get("needDormitory")),  # потребность в общежитии
                    is_bvi=bool(row.get("isBVI")),
                    is_active=bool(row.get("isActive", 1)),
                    raw=row,
                )
            )

        meta = ProgramMeta(
            title=comp.get("title") or comp.get("programSetTitle"),
            plan=to_int(comp.get("plan")),
            total=to_int(comp.get("appCount")) or len(entrants),
            min_score=to_num(comp.get("minScore")),
            min_score_all=to_num(comp.get("minScoreByAll")),
            updated_at=comp.get("updatedAt"),
            is_final=bool(comp.get("isFinal")),
        )
        return Snapshot(
            watch_id=watch.watch_id,
            meta=meta,
            entrants=entrants,
            fetched_at=now_iso(),
        )
