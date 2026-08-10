"""Probe every proxy's CF bypass service and report what it actually speaks.

BFR-024 Follow-Up asked for a startup reachability sweep over the pool's
bypass tier. This is that sweep, plus a protocol identification pass: the
spider only ever speaks CloudflareBypassForScraping's ``GET /html?url=``,
so a host running FlareSolverr (``POST /v1``) answers 404 and the failure
is invisible at INFO level. The probe dials both dialects and prints which
one the host answers, so a service swap is caught before a run burns the
pool.

Bypass requests follow the run's own ``CF_BYPASS_VIA_PROXY`` setting: tunnelled
through the proxy to ``127.0.0.1`` when it is on, dialled straight at the proxy
host's public bypass port when it is off. Probing the other topology would
report a healthy tier as down (or the reverse) purely because the probe dialled
somewhere production never does.

Usage:
    python3 -m apps.cli.ops.cf_bypass_probe
    python3 -m apps.cli.ops.cf_bypass_probe --proxy Singapore-ARM1 --verbose
    python3 -m apps.cli.ops.cf_bypass_probe --limit 5 --target https://javdb.com/
"""
from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, NamedTuple, Optional
from urllib.parse import quote, urlparse

import requests

from javdb.infra.request import RequestConfig, RequestHandler
from javdb.spider.html_validators import is_cf_challenge_page

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 90.0
FLARESOLVERR_PORT = 8191

# The target is fetched directly *and* handed to every proxy's bypass service,
# and response bodies are printed into a log the workflow uploads as an
# artifact. An unrestricted value would turn a read-only diagnostic into a
# fan-out fetcher for whatever the proxy hosts can reach, with the body
# exfiltrated to the artifact — so only the site this tool exists to diagnose
# is accepted.
ALLOWED_TARGET_HOSTS = ('javdb.com',)


def _validate_target(url: str) -> str:
    """Return *url* if it is an ``https://`` URL on an allowed host.

    Raises ``ValueError`` otherwise. IP literals and embedded credentials are
    rejected by the same host allowlist: an IP never matches a name in
    ``ALLOWED_TARGET_HOSTS``, and a ``user:pass@`` prefix is refused outright
    so credentials cannot leak into the uploaded probe log.
    """
    parsed = urlparse(url)
    if parsed.scheme != 'https':
        raise ValueError('must be an https:// URL')
    if parsed.username or parsed.password:
        raise ValueError('must not embed credentials')
    host = (parsed.hostname or '').lower()
    if not host:
        raise ValueError('must have a hostname')
    if not any(host == allowed or host.endswith('.' + allowed)
               for allowed in ALLOWED_TARGET_HOSTS):
        raise ValueError(
            'host must be ' + ' or '.join(ALLOWED_TARGET_HOSTS)
            + ' (or a subdomain)'
        )
    # ``urlparse.port`` raises on a non-numeric or out-of-range port rather
    # than returning None, so an unvalidated ``:abc``/``:0`` would surface as
    # an unhandled ValueError from somewhere much later in the probe.
    try:
        port = parsed.port
    except ValueError:
        raise ValueError('port must be an integer in 1-65535') from None
    if port is not None and not 1 <= port <= 65535:
        raise ValueError('port must be an integer in 1-65535')
    return url


def _parse_ports(raw: str) -> List[int]:
    """Parse ``--ports`` into a list of valid TCP ports.

    Raises ``ValueError`` with a message fit for ``parser.error``.
    """
    ports: List[int] = []
    for chunk in raw.split(','):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            port = int(chunk)
        except ValueError:
            raise ValueError(f'{chunk!r} is not an integer') from None
        if not 1 <= port <= 65535:
            raise ValueError(f'{port} is outside 1-65535')
        ports.append(port)
    return ports


@dataclass
class ProbeResult:
    """One dialect attempt against one proxy's bypass service."""

    label: str
    ok: bool = False
    status: Optional[int] = None
    size: int = 0
    content_type: str = ''
    note: str = ''
    body_head: str = ''
    markers: Dict[str, bool] = field(default_factory=dict)


def _classify(body: str) -> Dict[str, bool]:
    lowered = body.lower()
    return {
        'challenge': is_cf_challenge_page(body),
        'movie_list': 'movie-list' in lowered,
        'json': body.lstrip()[:1] in ('{', '['),
        'blocked_1020': 'error 1020' in lowered or 'you have been blocked' in lowered,
    }


def _entry_count(body: str) -> int:
    """Number of movie entries on an index page.

    A solver that returns the interstitial with a 200 still looks like a
    success by status and size alone, so accuracy has to be measured against
    the payload actually being parseable content.
    """
    return body.count('href="/v/')


def _record(label: str, resp: requests.Response) -> ProbeResult:
    body = resp.text or ''
    return ProbeResult(
        label=label,
        ok=resp.status_code == 200,
        status=resp.status_code,
        size=len(body),
        content_type=resp.headers.get('content-type', ''),
        body_head=body[:400].replace('\n', ' '),
        markers=_classify(body),
    )


def _proxies_for(entry: Dict[str, str]) -> Dict[str, str]:
    """Proxy dict that also covers http:// targets.

    ``_build_bypass_proxies`` mirrors https onto http for exactly this
    reason: the bypass URL is http://, and requests' ``select_proxy``
    keys on the *target* scheme, not the proxy's.
    """
    proxies = {k: v for k, v in entry.items() if k in ('http', 'https') and v}
    if 'http' not in proxies and 'https' in proxies:
        proxies['http'] = proxies['https']
    return proxies


def _bypass_handler() -> RequestHandler:
    """Handler used only to resolve bypass URLs the way production does.

    Every field is taken from the run's own config, ``CF_BYPASS_VIA_PROXY``
    included — pinning a topology here would make the probe answer a question
    nobody asked.
    """
    from javdb.spider.runtime.config import (
        CF_BYPASS_PORT_MAP, CF_BYPASS_SERVICE_PORT, CF_BYPASS_VIA_PROXY,
    )
    return RequestHandler(config=RequestConfig(
        cf_bypass_service_port=CF_BYPASS_SERVICE_PORT,
        cf_bypass_port_map=CF_BYPASS_PORT_MAP,
        cf_bypass_via_proxy=CF_BYPASS_VIA_PROXY,
    ))


class Endpoint(NamedTuple):
    """Where — and how — production would reach one proxy's bypass service."""

    host: str
    port: int
    proxies: Dict[str, str]

    def url(self, path: str, port: Optional[int] = None) -> str:
        return f'http://{self.host}:{port or self.port}{path}'

    def at_port(self, port: int) -> 'Endpoint':
        return Endpoint(self.host, port, self.proxies)


def _resolve_endpoint(entry: Dict[str, str], handler: RequestHandler) -> Endpoint:
    """Bypass endpoint production would use for this proxy.

    ``CF_BYPASS_PORT_MAP`` overrides the port per proxy IP, so a single port
    applied to the whole pool probes an endpoint nothing listens on for every
    overridden host — and the probe would report the tier as down when it is
    the probe that dialled wrong. Host and proxy routing likewise follow
    ``CF_BYPASS_VIA_PROXY``. All three are delegated to the handler rather
    than re-implemented.
    """
    proxy_url = entry.get('https') or entry.get('http') or ''
    proxy_ip = (
        RequestHandler.extract_ip_from_proxy_url(proxy_url) if proxy_url else None
    )
    parsed = urlparse(handler.get_cf_bypass_service_url(proxy_ip))
    return Endpoint(
        host=parsed.hostname or '127.0.0.1',
        port=parsed.port or handler.config.cf_bypass_service_port,
        proxies=handler._build_bypass_proxies(_proxies_for(entry), proxy_ip) or {},
    )


def _probe_service_root(endpoint: Endpoint) -> ProbeResult:
    """GET / — FlareSolverr answers a version banner here, CFBFS does not."""
    port = endpoint.port
    try:
        resp = requests.get(
            endpoint.url('/'), proxies=endpoint.proxies,
            timeout=(CONNECT_TIMEOUT, 20),
        )
    except requests.RequestException as exc:
        return ProbeResult(f'GET :{port}/', note=f'{type(exc).__name__}')
    result = _record(f'GET :{port}/', resp)
    try:
        payload = resp.json()
        version = payload.get('version') or payload.get('msg') or ''
        if version:
            result.note = f'banner={version!r}'
    except ValueError:
        pass
    return result


def _probe_cfbfs(endpoint: Endpoint, target: str) -> ProbeResult:
    """The dialect the spider actually speaks today.

    No ``x-hostname`` header: on CloudflareBypassForScraping v2 that header
    switches the server into request-mirroring mode, which would forward the
    literal path ``/html?url=...`` to the target host and get a 404 back from
    the site rather than a solved page.
    """
    port = endpoint.port
    url = endpoint.url(f'/html?url={quote(target, safe="")}')
    try:
        resp = requests.get(
            url, proxies=endpoint.proxies,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        )
    except requests.RequestException as exc:
        return ProbeResult(f'GET :{port}/html?url=', note=f'{type(exc).__name__}')
    return _record(f'GET :{port}/html?url=', resp)


def _probe_flaresolverr(endpoint: Endpoint, target: str) -> ProbeResult:
    """Native FlareSolverr dialect, unwrapping the JSON envelope."""
    port = endpoint.port
    payload = {'cmd': 'request.get', 'url': target, 'maxTimeout': 60000}
    try:
        resp = requests.post(
            endpoint.url('/v1'), json=payload, proxies=endpoint.proxies,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        )
    except requests.RequestException as exc:
        return ProbeResult(f'POST :{port}/v1', note=f'{type(exc).__name__}')

    result = _record(f'POST :{port}/v1', resp)
    try:
        envelope = resp.json()
    except ValueError:
        result.note = 'non-JSON response'
        return result

    solution = envelope.get('solution') or {}
    inner = solution.get('response') or ''
    result.size = len(inner)
    result.markers = _classify(inner)
    result.body_head = inner[:400].replace('\n', ' ')
    result.ok = envelope.get('status') == 'ok' and not result.markers['challenge']
    # "Challenge not detected!" with status ok is FlareSolverr issue #1737:
    # the interstitial is returned verbatim and reported as a success.
    result.note = (
        f"status={envelope.get('status')!r} msg={envelope.get('message')!r} "
        f"ua={(solution.get('userAgent') or '')[:60]!r} "
        f"cookies={len(solution.get('cookies') or [])}"
    )
    return result


def _probe_direct(proxies: Dict[str, str], target: str) -> ProbeResult:
    """Plain fetch through the proxy — the current site-wide challenge state."""
    headers = dict(RequestHandler.BROWSER_HEADERS)
    try:
        resp = requests.get(
            target, proxies=proxies, headers=headers,
            timeout=(CONNECT_TIMEOUT, 30),
        )
    except requests.RequestException as exc:
        return ProbeResult('DIRECT javdb.com', note=f'{type(exc).__name__}')
    return _record('DIRECT javdb.com', resp)


def probe_proxy(entry: Dict[str, str], endpoint: Endpoint,
                target: str) -> Dict[str, Any]:
    name = entry.get('name', '?')
    results = [
        _probe_direct(_proxies_for(entry), target),
        _probe_service_root(endpoint),
        _probe_cfbfs(endpoint, target),
    ]
    if endpoint.port != FLARESOLVERR_PORT:
        alt = endpoint.at_port(FLARESOLVERR_PORT)
        results.append(_probe_service_root(alt))
        results.append(_probe_flaresolverr(alt, target))
    else:
        results.append(_probe_flaresolverr(endpoint, target))
    return {'name': name, 'results': results}


def bench_endpoint(entry: Dict[str, str], endpoint: Endpoint, target: str,
                   trials: int) -> Dict[str, Any]:
    """Time `trials` sequential CFBFS-dialect fetches against one port.

    Sequential on purpose: these solvers drive a real browser, so concurrent
    trials against one host measure contention rather than solve time.
    """
    url = endpoint.url(f'/html?url={quote(target, safe="")}')
    samples: List[Dict[str, Any]] = []

    for _ in range(trials):
        started = time.monotonic()
        try:
            resp = requests.get(
                url, proxies=endpoint.proxies,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )
            body = resp.text or ''
            samples.append({
                'ms': (time.monotonic() - started) * 1000,
                'status': resp.status_code,
                'size': len(body),
                'entries': _entry_count(body),
                'challenge': is_cf_challenge_page(body),
                'error': None,
                'body_head': body[:200].replace('\n', ' '),
            })
        except requests.RequestException as exc:
            samples.append({
                'ms': (time.monotonic() - started) * 1000,
                'status': None, 'size': 0, 'entries': 0, 'challenge': False,
                'error': type(exc).__name__, 'body_head': '',
            })

    good = [s for s in samples if s['status'] == 200
            and not s['challenge'] and s['entries'] > 0]
    latencies = sorted(s['ms'] for s in good) or [0.0]
    bad = [s for s in samples if s not in good]
    return {
        'name': entry.get('name', '?'),
        'port': endpoint.port,
        'trials': trials,
        'ok': len(good),
        # First failing payload, so a wrong endpoint or a solver returning the
        # interstitial is diagnosable without a second run.
        'failure_sample': bad[0].get('body_head', '') if bad else '',
        'challenge': sum(1 for s in samples if s['challenge']),
        'errors': sorted({s['error'] for s in samples if s['error']}),
        'statuses': sorted({s['status'] for s in samples if s['status']}),
        'p50_ms': statistics.median(latencies),
        'max_ms': max(latencies),
        'entries_median': (
            statistics.median(sorted(s['entries'] for s in good)) if good else 0
        ),
    }


def _format_bench(rows: List[Dict[str, Any]]) -> str:
    lines = [
        '',
        f"{'proxy':<18}{'port':>6}{'ok':>8}{'p50 ms':>10}{'max ms':>10}"
        f"{'entries':>9}  notes",
        '─' * 78,
    ]
    for r in sorted(rows, key=lambda x: (x['port'], x['name'])):
        notes = []
        if r['challenge']:
            notes.append(f"challenge×{r['challenge']}")
        if r['errors']:
            notes.append(','.join(r['errors']))
        if [s for s in r['statuses'] if s != 200]:
            notes.append('http=' + ','.join(
                str(s) for s in r['statuses'] if s != 200))
        lines.append(
            f"{r['name']:<18}{r['port']:>6}{r['ok']:>4}/{r['trials']:<3}"
            f"{r['p50_ms']:>10.0f}{r['max_ms']:>10.0f}"
            f"{r['entries_median']:>9.0f}  {' '.join(notes)}"
        )

    for r in sorted(rows, key=lambda x: (x['port'], x['name'])):
        if r['ok'] < r['trials'] and r.get('failure_sample'):
            lines.append(
                f"  {r['name']} :{r['port']} first failure body: "
                f"{r['failure_sample'][:180]}"
            )

    lines.append('')
    for port in sorted({r['port'] for r in rows}):
        subset = [r for r in rows if r['port'] == port]
        ok = sum(r['ok'] for r in subset)
        total = sum(r['trials'] for r in subset)
        succeeded = [r for r in subset if r['ok']]
        p50 = statistics.median(
            sorted(r['p50_ms'] for r in succeeded)) if succeeded else 0
        lines.append(
            f"  port {port}: accuracy {ok}/{total} "
            f"({100 * ok / total if total else 0:.0f}%) · median p50 {p50:.0f} ms"
        )
    return '\n'.join(lines)


def _format(report: Dict[str, Any], verbose: bool) -> str:
    lines = [f"\n=== {report['name']} ==="]
    for res in report['results']:
        flags = ' '.join(k for k, v in res.markers.items() if v) or '-'
        lines.append(
            f"  {res.label:26s} status={str(res.status or '-'):>4s} "
            f"size={res.size:>7d} markers={flags}"
            + (f" | {res.note}" if res.note else '')
        )
        if verbose and res.body_head:
            lines.append(f"      body: {res.body_head[:300]}")
    return '\n'.join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proxy', action='append', default=[],
                        help='Probe only these proxy names (repeatable).')
    parser.add_argument('--limit', type=int, default=0,
                        help='Probe at most N proxies (0 = all).')
    parser.add_argument('--target', default='https://javdb.com/',
                        help='URL to ask the bypass service for.')
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--verbose', action='store_true',
                        help='Print the first 300 chars of each response body.')
    parser.add_argument('--json', action='store_true',
                        help='Emit machine-readable JSON instead of a table.')
    parser.add_argument('--bench', type=int, default=0, metavar='N',
                        help='Benchmark mode: N sequential trials per proxy '
                             'per port, reporting accuracy and latency.')
    parser.add_argument('--ports', default='',
                        help='Comma-separated bypass ports to benchmark '
                             '(default: the port CF_BYPASS_PORT_MAP / '
                             'CF_BYPASS_SERVICE_PORT resolve for each proxy).')
    args = parser.parse_args(argv)

    if args.workers <= 0:
        parser.error('--workers must be greater than 0')
    if args.limit < 0:
        parser.error('--limit must be 0 or greater')
    if args.bench < 0:
        parser.error('--bench must be 0 or greater')
    try:
        explicit_ports = _parse_ports(args.ports)
    except ValueError as exc:
        parser.error(f'--ports: {exc}')
    try:
        _validate_target(args.target)
    except ValueError as exc:
        parser.error(f'--target: {exc}')

    logging.basicConfig(level=logging.INFO, format='%(message)s')

    from javdb.spider.runtime.config import PROXY_POOL

    pool = list(PROXY_POOL or [])
    if args.proxy:
        wanted = set(args.proxy)
        pool = [p for p in pool if p.get('name') in wanted]
    if args.limit:
        pool = pool[:args.limit]
    if not pool:
        print('No proxies selected — check PROXY_POOL and --proxy/--limit.')
        return 1

    handler = _bypass_handler()
    targets = [(entry, _resolve_endpoint(entry, handler)) for entry in pool]

    if args.bench:
        # --ports exists precisely to compare two ports on one host, so an
        # explicit value overrides the per-proxy resolution.
        if explicit_ports:
            jobs = [
                (entry, endpoint.at_port(port))
                for entry, endpoint in targets for port in explicit_ports
            ]
            port_note = f'{len(explicit_ports)} port(s)'
        else:
            jobs = list(targets)
            port_note = 'resolved port per proxy'
        print(f'Benchmarking {len(pool)} proxies × {port_note} × '
              f'{args.bench} trials · target {args.target}')
        with ThreadPoolExecutor(max_workers=args.workers) as pool_exec:
            rows = list(pool_exec.map(
                lambda job: bench_endpoint(job[0], job[1], args.target, args.bench),
                jobs,
            ))
        if args.json:
            print(json.dumps(rows, indent=2, ensure_ascii=False))
        else:
            print(_format_bench(rows))
        return 0

    ports_seen = ','.join(str(p) for p in sorted({e.port for _, e in targets}))
    print(f'Probing {len(pool)} proxies · bypass port(s) {ports_seen} '
          f'· target {args.target}')
    if handler.config.cf_bypass_via_proxy:
        print('Bypass requests are tunnelled through the proxy to 127.0.0.1 '
              '(CF_BYPASS_VIA_PROXY=True); ports come from CF_BYPASS_PORT_MAP '
              'where set.')
    else:
        print('Bypass requests are dialled straight at each proxy host '
              '(CF_BYPASS_VIA_PROXY=False); ports come from '
              'CF_BYPASS_PORT_MAP where set.')

    with ThreadPoolExecutor(max_workers=args.workers) as pool_exec:
        reports = list(pool_exec.map(
            lambda job: probe_proxy(job[0], job[1], args.target),
            targets,
        ))

    if args.json:
        print(json.dumps(
            [{'name': r['name'],
              'results': [vars(x) for x in r['results']]} for r in reports],
            indent=2, ensure_ascii=False,
        ))
        return 0

    for report in reports:
        print(_format(report, args.verbose))

    print('\n──── SUMMARY ────')
    for report in reports:
        verdict = [res.label for res in report['results'] if res.ok]
        print(f"  {report['name']:22s} answered: {', '.join(verdict) or 'NOTHING'}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
