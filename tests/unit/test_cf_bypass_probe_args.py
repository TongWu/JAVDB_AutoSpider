"""Argument validation and per-proxy port resolution for the CF bypass probe.

The probe fans its ``--target`` out to every proxy's bypass service and prints
response bodies into a log the workflow uploads as an artifact, so the target
allowlist is load-bearing rather than cosmetic. Port resolution matters for a
different reason: the probe advertises itself as mirroring production's
topology, and ``CF_BYPASS_PORT_MAP`` moves the port per proxy — probing one
port across the pool would report an overridden host's healthy tier as down.
"""
import pytest

from apps.cli.ops.cf_bypass_probe import (
    _parse_ports,
    _resolve_endpoint,
    _validate_target,
    main,
)
from javdb.infra.request import RequestConfig, RequestHandler


def _handler(port_map=None, port=8000, via_proxy=True):
    return RequestHandler(config=RequestConfig(
        cf_bypass_service_port=port,
        cf_bypass_port_map=port_map or {},
        cf_bypass_via_proxy=via_proxy,
    ))


@pytest.mark.parametrize("url", [
    "https://javdb.com/",
    "https://javdb.com/?page=1",
    "https://www.javdb.com/v/abc",
])
def test_validate_target_accepts_javdb(url):
    assert _validate_target(url) == url


@pytest.mark.parametrize("url", [
    "http://javdb.com/",              # plaintext
    "file:///etc/passwd",             # non-http scheme
    "https://evil.com/",              # other host
    "https://notjavdb.com/",          # suffix without the dot boundary
    "https://javdb.com.evil.com/",    # allowed host as a prefix label
    "https://user:pass@javdb.com/",   # credentials would leak into the log
    "https://127.0.0.1/",             # IP literal
    "https://[::1]/",                 # IPv6 literal
    "https://javdb.com:0/",           # port below the valid range
    "https://javdb.com:65536/",       # port above the valid range
    "https://javdb.com:abc/",         # non-numeric port
])
def test_validate_target_rejects(url):
    with pytest.raises(ValueError):
        _validate_target(url)


def test_parse_ports_accepts_list_and_blanks():
    assert _parse_ports("8000, 8002 ,") == [8000, 8002]
    assert _parse_ports("") == []


@pytest.mark.parametrize("raw", ["abc", "8000,abc", "0", "70000", "-1"])
def test_parse_ports_rejects(raw):
    with pytest.raises(ValueError):
        _parse_ports(raw)


def test_resolve_port_defaults_to_service_port():
    entry = {"name": "P1", "http": "http://10.0.0.9:7890"}
    assert _resolve_endpoint(entry, _handler()).port == 8000


def test_resolve_port_honours_port_map():
    """A mapped proxy must be probed on its own port, not the pool default."""
    entry = {"name": "P1", "https": "http://10.0.0.5:7890"}
    assert _resolve_endpoint(entry, _handler({"10.0.0.5": 9001})).port == 9001


def test_resolve_port_ignores_map_for_other_proxies():
    entry = {"name": "P2", "https": "http://10.0.0.6:7890"}
    assert _resolve_endpoint(entry, _handler({"10.0.0.5": 9001})).port == 8000


def test_via_proxy_tunnels_to_loopback_through_the_proxy():
    """CF_BYPASS_VIA_PROXY=True: dial 127.0.0.1 *through* the proxy."""
    entry = {"name": "P1", "https": "http://10.0.0.5:7890"}
    endpoint = _resolve_endpoint(entry, _handler(via_proxy=True))

    assert endpoint.host == "127.0.0.1"
    assert endpoint.url("/html") == "http://127.0.0.1:8000/html"
    assert endpoint.proxies.get("http") == "http://10.0.0.5:7890"


def test_direct_dial_when_via_proxy_is_off():
    """CF_BYPASS_VIA_PROXY=False: dial the proxy host's public bypass port.

    Pinning the tunnelled topology here would make the probe report a healthy
    tier as down purely because it dialled somewhere production never does.
    """
    entry = {"name": "P1", "https": "http://10.0.0.5:7890"}
    endpoint = _resolve_endpoint(entry, _handler(via_proxy=False))

    assert endpoint.host == "10.0.0.5"
    assert endpoint.url("/html") == "http://10.0.0.5:8000/html"
    assert not endpoint.proxies


def test_at_port_keeps_host_and_routing():
    """The FlareSolverr comparison probe must reuse the same topology."""
    entry = {"name": "P1", "https": "http://10.0.0.5:7890"}
    endpoint = _resolve_endpoint(entry, _handler(via_proxy=False))
    alt = endpoint.at_port(8191)

    assert (alt.host, alt.port) == ("10.0.0.5", 8191)
    assert alt.proxies == endpoint.proxies


@pytest.mark.parametrize("argv", [
    ["--workers", "0"],
    ["--limit", "-1"],
    ["--bench", "-3"],
    ["--ports", "abc"],
    ["--target", "https://evil.com/"],
])
def test_main_rejects_bad_arguments(argv):
    """Validation happens in main() before any request is made."""
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
