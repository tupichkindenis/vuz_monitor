"""Telegram message building — focus on the ВП-line skip for flag-less sources (МАИ)."""
from vuz_monitor import notify
from vuz_monitor.diff import CodeStatus
from vuz_monitor.report import CodeReport, WatchReport


def _status(**kw):
    d = dict(
        code_display="1366129", present=True, place=176, priority=5, final_score=258.0,
        consent=False, passing_main=None, passing_real=None, paid_ok=None,
        contract=None, payment=None, needs_dormitory=None, ahead=0, total=1271, plan=None,
    )
    d.update(kw)
    return CodeStatus(**d)


def _msg(group, status, title=None):
    rep = WatchReport(name="ИВТ", title=title or group, group=group,
                      codes=[CodeReport(status=status, changes=[], first_run=False)])
    return "\n".join(notify.build_messages([(group, [rep])]))


def test_budget_no_vp_flags_omits_vp_line():
    m = _msg("МАИ — бюджет", _status())
    assert "ВП прох./основ." not in m       # МАИ has no ВП flags → line omitted
    assert "место: 176 из 1271" in m
    assert "балл: 258" in m
    assert "Согласие: нет" in m


def test_paid_no_vp_flags_omits_vp_line_uses_dogovor():
    m = _msg("МАИ — платно", _status(contract=False, consent=False), title="платно")
    assert "ВП прох./основ." not in m
    assert "Соблюдены условия для платного: нет" in m
    assert "договор: нет" in m


def test_vp_line_still_shown_when_flags_present():
    # regression: sources WITH flags (МИРЭА/Станкин) must still print the ВП line
    m = _msg("МИРЭА — бюджет", _status(passing_real=False, passing_main=True))
    assert "ВП прох./основ." in m
