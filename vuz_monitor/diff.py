"""Standing computation + hour-over-hour diff for a tracked applicant code.

Passing status uses MIREA's OFFICIAL flags (no home-grown estimate):
  - Проходной ВП (`passing_real`) — would be admitted per CURRENT consents (reality now).
  - Основной ВП  (`passing_main`) — guaranteed if you consent in time (models all-consent).
Both already account for priorities across programs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .models import Snapshot


@dataclass
class CodeStatus:
    code_display: str
    present: bool
    place: Optional[int]
    priority: Optional[int]
    final_score: Optional[float]
    consent: bool                       # согласие на зачисление
    passing_main: Optional[bool]        # Основной ВП
    passing_real: Optional[bool]        # Проходной ВП
    paid_ok: Optional[bool]             # условия для платного
    contract: Optional[bool]            # МЭИ paid: договор
    payment: Optional[bool]             # МЭИ paid: оплата
    needs_dormitory: Optional[bool]     # потребность в общежитии
    ahead: int                          # entrants ranked above (context)
    total: Optional[int]
    plan: Optional[int]


@dataclass
class CodeChange:
    field: str
    old: Any
    new: Any


# Status fields compared hour-over-hour, in message display order.
_TRACKED_FIELDS = (
    "present",
    "place",
    "final_score",
    "priority",
    "consent",
    "contract",
    "payment",
    "passing_real",
    "passing_main",
    "paid_ok",
)


def compute_status(
    snap: Optional[Snapshot], code: str, plan_override: Optional[int] = None
) -> Optional[CodeStatus]:
    """Standing of ``code`` in ``snap``. Returns None if ``snap`` is None."""
    if snap is None:
        return None

    plan = plan_override if plan_override is not None else snap.meta.plan
    total = snap.meta.total  # None when the source doesn't publish a size (e.g. Станкин)
    e = snap.by_code(code)

    if e is None:
        return CodeStatus(
            code_display=code, present=False, place=None, priority=None,
            final_score=None, consent=False, passing_main=None, passing_real=None,
            paid_ok=None, contract=None, payment=None, needs_dormitory=None,
            ahead=0, total=total, plan=plan,
        )

    ahead = (
        sum(1 for x in snap.entrants if x.place is not None and e.place is not None and x.place < e.place)
        if e.place is not None
        else 0
    )
    return CodeStatus(
        code_display=e.code_display,
        present=True,
        place=e.place,
        priority=e.priority,
        final_score=e.final_score,
        consent=bool(e.consent),
        passing_main=e.passing_main,
        passing_real=e.passing_real,
        paid_ok=e.paid_ok,
        contract=e.contract,
        payment=e.payment,
        needs_dormitory=e.needs_dormitory,
        ahead=ahead,
        total=total,
        plan=plan,
    )


def compute_changes(
    prev: Optional[CodeStatus], new: Optional[CodeStatus]
) -> list:
    """List field-level changes from ``prev`` to ``new``. Empty on first run."""
    if prev is None or new is None:
        return []
    changes = []
    for f in _TRACKED_FIELDS:
        old_v = getattr(prev, f)
        new_v = getattr(new, f)
        if old_v != new_v:
            changes.append(CodeChange(field=f, old=old_v, new=new_v))
    return changes
