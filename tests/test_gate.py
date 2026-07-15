import errno
import httpx

from vuz_monitor.config import WatchConfig, AppConfig, TelegramConfig
from vuz_monitor import pipeline


def _watch(name, url):
    return WatchConfig(name=name, adapter="fake", url=url, params={"k": name}, codes=["100"])


def _cfg(watches):
    return AppConfig(telegram=TelegramConfig(chat_id="1", bot_token="t"),
                     heartbeat="always", tracked_codes=["100"], watches=watches, db_path=":memory:")


def _conn_err():
    e = httpx.ConnectError("x"); e.__cause__ = OSError(errno.EHOSTUNREACH, "no route"); return e


def _patch_all_fail(monkeypatch, exc):
    class _A:
        def fetch(self, w): raise exc
    monkeypatch.setattr(pipeline, "get_adapter", lambda name: _A())
    monkeypatch.setattr(pipeline, "_render_dashboard", lambda *a, **k: [])  # hermetic: no docs/ writes


def test_gate_fires_when_all_connectivity_across_two_hosts(monkeypatch):
    sent = []
    monkeypatch.setattr(pipeline.notify, "send_message", lambda *a, **k: sent.append(a))
    _patch_all_fail(monkeypatch, _conn_err())
    cfg = _cfg([_watch("a", "https://host.one/x"), _watch("b", "https://host.two/x")])
    rc = pipeline.run(cfg, dry_run=False)
    assert rc == 0 and sent == []            # nothing sent


def test_gate_does_not_fire_single_host(monkeypatch):
    sent = []
    monkeypatch.setattr(pipeline.notify, "send_message", lambda *a, **k: sent.append(a))
    _patch_all_fail(monkeypatch, _conn_err())
    cfg = _cfg([_watch("a", "https://only.host/x"), _watch("b", "https://only.host/y")])
    pipeline.run(cfg, dry_run=False)
    assert sent != []                        # one host → gate off → error message attempted


def test_gate_off_in_dry_run(monkeypatch, capsys):
    _patch_all_fail(monkeypatch, _conn_err())
    cfg = _cfg([_watch("a", "https://host.one/x"), _watch("b", "https://host.two/x")])
    pipeline.run(cfg, dry_run=True)
    assert "недоступно" in capsys.readouterr().out   # dry-run still renders the ⏳ sections
