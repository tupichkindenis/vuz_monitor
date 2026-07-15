import errno
import socket

import httpx
import pytest

from vuz_monitor.adapters.base import is_connectivity_error


def _wrap(cause):
    """A ConnectError with `cause` in its __cause__ chain, like httpx produces."""
    err = httpx.ConnectError("connect failed")
    err.__cause__ = cause
    return err


@pytest.mark.parametrize("errno_val", [errno.EHOSTUNREACH, errno.ENETUNREACH, errno.ENETDOWN, errno.ECANCELED])
def test_net_errnos_are_connectivity(errno_val):
    assert is_connectivity_error(_wrap(OSError(errno_val, "boom"))) is True


def test_dns_gaierror_is_connectivity():
    assert is_connectivity_error(_wrap(socket.gaierror(8, "nodename nor servname provided"))) is True


def test_connection_refused_is_source():
    assert is_connectivity_error(_wrap(OSError(errno.ECONNREFUSED, "Connection refused"))) is False


def test_plain_oserror_errno_8_is_not_dns():
    # errno 8 as a bare OSError is ENOEXEC, unrelated to DNS
    assert is_connectivity_error(OSError(8, "Exec format error")) is False


def test_connect_timeout_is_ambiguous_false():
    assert is_connectivity_error(httpx.ConnectTimeout("timed out")) is False


def test_http_status_and_value_error_are_false():
    req = httpx.Request("GET", "http://x")
    resp = httpx.Response(500, request=req)
    assert is_connectivity_error(httpx.HTTPStatusError("500", request=req, response=resp)) is False
    assert is_connectivity_error(ValueError("bad table")) is False


def test_cyclic_cause_chain_terminates():
    a, b = OSError("a"), OSError("b")
    a.__cause__, b.__cause__ = b, a
    assert is_connectivity_error(a) is False
