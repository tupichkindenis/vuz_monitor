import errno
import httpx
import pytest

from vuz_monitor import notify
from vuz_monitor.report import WatchReport


def test_api_call_raises_telegram_network_error_on_connectivity(monkeypatch):
    def _boom(*a, **k):
        err = httpx.ConnectError("x"); err.__cause__ = OSError(errno.EHOSTUNREACH, "no route")
        raise err
    monkeypatch.setattr(notify.httpx, "post", _boom)
    with pytest.raises(notify.TelegramNetworkError):
        notify._api_call("tok", "sendMessage", json={"chat_id": "1", "text": "hi"})


def test_api_call_non_connectivity_stays_plain_runtimeerror(monkeypatch):
    def _boom(*a, **k):
        raise httpx.ReadTimeout("slow")
    monkeypatch.setattr(notify.httpx, "post", _boom)
    with pytest.raises(RuntimeError) as ei:
        notify._api_call("tok", "sendMessage", json={"chat_id": "1", "text": "hi"})
    assert not isinstance(ei.value, notify.TelegramNetworkError)


def test_specialty_block_net_error_is_calm():
    rep = WatchReport(name="Программная инженерия", error="[Errno 65] No route to host", net_error=True)
    out = notify._specialty_block(rep, show_code=False)
    assert "временно недоступно" in out
    assert "[Errno" not in out


def test_specialty_block_source_error_keeps_oshibka():
    rep = WatchReport(name="X", error="table not found", net_error=False)
    out = notify._specialty_block(rep, show_code=False)
    assert "ошибка" in out
