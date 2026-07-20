"""Normalized data model shared by every adapter.

Adapters translate a source-specific response into these dataclasses, so the diff
and notify layers never care where the data came from.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, fields, asdict
from typing import Any, Optional

_NON_DIGITS = re.compile(r"\D+")


def normalize_code(raw: Any) -> str:
    """Reduce a СНИЛС / application number to digits only, for stable matching.

    ``"166-172-036 59"`` and ``"16617203659"`` both become ``"16617203659"`` so
    formatting differences between config and source never break the lookup.
    """
    return _NON_DIGITS.sub("", str(raw or ""))


@dataclass
class Entrant:
    code: str                              # normalized (digits only) — match key
    code_display: str                      # original code as published
    place: Optional[int] = None            # rank in the list, 1 = top
    final_score: Optional[float] = None
    entrance_score: Optional[float] = None
    achievement_score: Optional[float] = None
    priority: Optional[int] = None
    consent: bool = False                  # согласие на зачисление submitted (accepted)
    # Official MIREA "would be admitted" flags (None if the source doesn't provide them):
    passing_main: Optional[bool] = None    # Основной ВП: guaranteed if consent given in time (models all-consent)
    passing_real: Optional[bool] = None    # Проходной ВП: would be admitted per CURRENT consents
    paid_ok: Optional[bool] = None         # Соблюдены условия для платного: contract signed + paid
    contract: Optional[bool] = None        # МЭИ paid: «Договор» (contract signed)
    payment: Optional[bool] = None         # МЭИ paid: «Оплата» (payment made)
    needs_dormitory: Optional[bool] = None # Потребность в общежитии (shown on budget lists)
    is_bvi: bool = False                   # без вступительных испытаний
    is_active: bool = True
    raw: dict = field(default_factory=dict)


@dataclass
class ProgramMeta:
    title: Optional[str] = None
    plan: Optional[int] = None             # budget places (КЦП)
    total: Optional[int] = None            # total applicants
    min_score: Optional[float] = None      # Проходной ВП floor now (API minScore)
    min_score_all: Optional[float] = None  # guaranteed floor if all consent (API minScoreByAll = Основной ВП)
    updated_at: Optional[str] = None       # source's own timestamp; change signal
    is_final: bool = False


@dataclass
class Snapshot:
    watch_id: str
    meta: ProgramMeta
    entrants: list
    fetched_at: str

    def by_code(self, code: str) -> Optional[Entrant]:
        norm = normalize_code(code)
        for e in self.entrants:
            if e.code == norm:
                return e
        return None


def _kwargs_for(cls, d: dict) -> dict:
    """Keep only keys that are real fields of ``cls`` (schema-drift safe)."""
    allowed = {f.name for f in fields(cls)}
    return {k: v for k, v in d.items() if k in allowed}


def snapshot_to_dict(s: Snapshot) -> dict:
    return {
        "watch_id": s.watch_id,
        "fetched_at": s.fetched_at,
        "meta": asdict(s.meta),
        "entrants": [asdict(e) for e in s.entrants],
    }


def snapshot_from_dict(d: dict) -> Snapshot:
    meta = ProgramMeta(**_kwargs_for(ProgramMeta, d.get("meta", {})))
    entrants = [Entrant(**_kwargs_for(Entrant, e)) for e in d.get("entrants", [])]
    return Snapshot(
        watch_id=d["watch_id"],
        meta=meta,
        entrants=entrants,
        fetched_at=d.get("fetched_at", ""),
    )
