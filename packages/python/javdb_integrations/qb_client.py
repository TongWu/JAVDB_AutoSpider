"""Shared qBittorrent client helpers.

A single place for the qBittorrent Web API wrapper used by pikpak_bridge,
qb_file_filter and any future tooling. Historically each consumer
re-implemented login, list and delete calls; consolidating them here
keeps the behaviour (TLS handling, URL fallback, masking, logging)
consistent.

Typical usage:

    from packages.python.javdb_integrations.qb_client import (
        QBittorrentClient,
        remove_completed_torrents_keep_files,
    )

    qb = QBittorrentClient(qb_base_url_candidates(), user, pw)
    remove_completed_torrents_keep_files(qb, ["JavDB", "Ad Hoc"])
"""

from __future__ import annotations

from typing import Callable, Iterable, List, Optional, Tuple

import requests

from packages.python.javdb_core.masking import mask_error, mask_ip_address, mask_username
from packages.python.javdb_platform.logging_config import get_logger
from packages.python.javdb_platform.qb_config import (
    masked_qb_base_url as _masked_qb_base_url,
    qb_verify_tls,
)


logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Module-level login / ping helpers
#
# These are extracted so the three qB consumers (the QBittorrentClient class
# itself, qb_uploader and qb_file_filter) all share the same URL-fallback
# state machine. The helpers take ``requester``/``get``/``post`` callables
# as dependencies, which keeps consumers' mocking surface (e.g. patching
# ``scripts.qb_uploader.requests.get``) intact and avoids forcing every
# caller into the class API.
# ---------------------------------------------------------------------------


def try_ping_base_urls(
    base_urls: Iterable[str],
    get_fn: Callable,
    *,
    allow_insecure_http: bool = False,
    proxies: Optional[dict] = None,
    timeout: Optional[float] = 10,
    verify: bool = True,
) -> Tuple[Optional[str], Optional[BaseException]]:
    """Try ``GET {base_url}/api/v2/app/version`` against each candidate
    base URL in order; return ``(reachable_url, None)`` on the first
    response whose status code is 200 or 403, else ``(None, last_error)``.

    qB returns 403 for unauthenticated /app/version requests; that still
    proves the endpoint is reachable, which is why both codes are treated
    as success.
    """
    candidates = [str(u).rstrip("/") for u in base_urls if str(u).strip()]
    primary = candidates[0] if candidates else None
    last_error: Optional[BaseException] = None

    for base_url in candidates:
        masked_url = _masked_qb_base_url(
            base_url, allow_insecure_http=allow_insecure_http
        )
        try:
            logger.info(f"Testing connection to qBittorrent at {masked_url}")
            response = get_fn(
                f"{base_url}/api/v2/app/version",
                timeout=timeout,
                proxies=proxies,
                verify=verify,
            )
        except requests.RequestException as exc:
            last_error = exc
            logger.warning(
                f"Connection attempt failed for {masked_url}: "
                f"{mask_error(str(exc))}"
            )
            continue

        if response.status_code in (200, 403):
            if base_url != primary:
                logger.info(
                    f"qBittorrent is accessible after retrying over HTTP at "
                    f"{masked_url}"
                )
            else:
                logger.info("qBittorrent is accessible")
            return base_url, None
        logger.warning(
            f"qBittorrent responded with status code {response.status_code} "
            f"at {masked_url}"
        )

    if last_error is not None:
        logger.error(
            f"Cannot connect to qBittorrent: {mask_error(str(last_error))}"
        )
    return None, last_error


LOGIN_SUCCESS = "success"          # first candidate that returned 200/Ok.
LOGIN_REJECTED = "rejected"        # 401/403 or 'Fails.' — credentials wrong
LOGIN_UNREACHABLE = "unreachable"  # network errors / non-auth statuses


def try_login_base_urls(
    base_urls: Iterable[str],
    username: str,
    password: str,
    post_fn: Callable,
    *,
    allow_insecure_http: bool = False,
    proxies: Optional[dict] = None,
    timeout: Optional[float] = None,
    verify: bool = True,
) -> Tuple[str, Optional[str], Optional[BaseException]]:
    """Try ``POST {base_url}/api/v2/auth/login`` against each candidate in
    order.

    Returns ``(outcome, successful_base_url, last_error)`` where ``outcome``
    is one of ``LOGIN_SUCCESS`` / ``LOGIN_REJECTED`` / ``LOGIN_UNREACHABLE``.
    On ``LOGIN_REJECTED`` the caller should stop retrying (credentials are
    wrong — no amount of URL fallback will fix that).
    """
    candidates = [str(u).rstrip("/") for u in base_urls if str(u).strip()]
    primary = candidates[0] if candidates else None
    last_error: Optional[BaseException] = None

    for base_url in candidates:
        masked_url = _masked_qb_base_url(
            base_url, allow_insecure_http=allow_insecure_http
        )
        try:
            logger.info(
                f"Attempting to login to qBittorrent at {masked_url} as "
                f"{mask_username(username)}"
            )
            response = post_fn(
                f"{base_url}/api/v2/auth/login",
                data={"username": username, "password": password},
                timeout=timeout,
                proxies=proxies,
                verify=verify,
            )
        except requests.RequestException as exc:
            last_error = exc
            logger.warning(
                f"Login error at {masked_url}: {mask_error(str(exc))}"
            )
            continue

        if response.status_code == 200 and response.text == "Ok.":
            if base_url != primary:
                logger.info(
                    f"qBittorrent HTTPS login failed; retried successfully "
                    f"over HTTP at {masked_url}."
                )
            logger.info("Successfully logged in to qBittorrent")
            return LOGIN_SUCCESS, base_url, None

        logger.error(
            f"Login failed with status code {response.status_code} at "
            f"{masked_url}"
        )
        # Only qBittorrent's own credential-rejection response ("Fails.") is
        # treated as a definitive credentials-wrong signal that should stop
        # the URL-fallback walk. Bare 401/403 status codes are deliberately
        # NOT treated as credential rejection here, because an edge
        # front-end (e.g. a reverse proxy or WAF in front of HTTPS) can
        # return 403 while a later HTTP candidate would actually accept
        # the login. Stopping early in that case would regress the
        # HTTPS→HTTP fallback that qb_base_url_candidates() exists to
        # enable. See review note: "403 on first URL should not hard-fail
        # when later candidates may succeed."
        if isinstance(response.text, str) and response.text.strip() == "Fails.":
            return LOGIN_REJECTED, None, Exception(response.text)

        last_error = Exception(response.text)

    return LOGIN_UNREACHABLE, None, last_error


class QBittorrentClient:
    """Thin wrapper around qBittorrent's Web API.

    The constructor accepts a list of base-URL candidates (e.g. both
    https:// and http:// variants) and picks whichever one accepts the
    login. ``proxies_getter`` is an optional callable returning a
    ``requests``-style proxies dict — it's called once at construction
    time and its result is reused for every request. Consumers that want
    proxying wire this up to their usual ``get_proxies_dict`` helper.
    """

    @classmethod
    def from_existing_session(
        cls,
        session: requests.Session,
        base_url: str,
        proxies: Optional[dict] = None,
        request_timeout: Optional[float] = None,
    ) -> "QBittorrentClient":
        """Wrap an already-authenticated ``requests.Session`` without
        performing a new login. Used by qb_file_filter / qb_uploader,
        which manage their own session+login lifecycle but want to reuse
        the shared API helpers.

        The session's ``verify`` flag is aligned with :func:`qb_verify_tls`
        so that deployments using self-signed or internal-CA certificates
        (``QB_VERIFY_TLS=false``) continue to work for follow-up API
        calls made through the shared client — otherwise
        :class:`requests.Session` defaults to verifying TLS regardless of
        how the caller performed the initial login.
        """
        obj = cls.__new__(cls)
        obj.base_urls = [base_url.rstrip("/")]
        obj.base_url = obj.base_urls[0]
        obj.session = session
        obj.session.verify = qb_verify_tls()
        obj.use_proxy = False
        obj.proxies = proxies
        obj.request_timeout = request_timeout
        return obj

    def __init__(
        self,
        base_urls,
        username: str,
        password: str,
        use_proxy: bool = False,
        proxies_getter=None,
        request_timeout: Optional[float] = None,
    ) -> None:
        if isinstance(base_urls, str):
            self.base_urls: List[str] = [base_urls.rstrip("/")]
        else:
            self.base_urls = [
                str(url).rstrip("/") for url in base_urls if str(url).strip()
            ]
        if not self.base_urls:
            raise ValueError("QBittorrentClient requires at least one base URL")

        self.base_url = self.base_urls[0]
        self.session = requests.Session()
        self.session.verify = qb_verify_tls()
        self.use_proxy = use_proxy
        self.request_timeout = request_timeout

        if proxies_getter is not None:
            try:
                self.proxies = proxies_getter(use_proxy)
            except TypeError:
                self.proxies = proxies_getter()
        else:
            self.proxies = None

        self.login(username, password)

    def _request_kwargs(self) -> dict:
        kwargs = {"proxies": self.proxies}
        if self.request_timeout is not None:
            kwargs["timeout"] = self.request_timeout
        return kwargs

    def login(self, username: str, password: str) -> None:
        def _post(url, **kwargs):
            # Keep ``verify`` out of kwargs: the session-level verify flag
            # already applies. Drop ``timeout=None`` so requests uses its
            # default when no timeout was configured.
            kwargs.pop("verify", None)
            if kwargs.get("timeout") is None:
                kwargs.pop("timeout", None)
            return self.session.post(url, **kwargs)

        outcome, url, err = try_login_base_urls(
            self.base_urls,
            username,
            password,
            post_fn=_post,
            proxies=self.proxies,
            timeout=self.request_timeout,
            verify=self.session.verify,
        )
        if outcome == LOGIN_SUCCESS and url:
            self.base_url = url
            return
        if err is None:
            raise Exception("Failed to login qBittorrent")
        raise Exception(f"Failed to login qBittorrent: {err}")

    def get_torrents(
        self, category: Optional[str] = None, torrent_filter: str = "downloading"
    ) -> list:
        """Return torrents in the given category matching ``filter``.

        When ``category`` is None, qB returns torrents across all
        categories (this matches the Web API behaviour).
        """
        params = {"filter": torrent_filter}
        if category is not None:
            params["category"] = category
        resp = self.session.get(
            f"{self.base_url}/api/v2/torrents/info",
            params=params,
            **self._request_kwargs(),
        )
        resp.raise_for_status()
        return resp.json()

    def test_connection(self) -> bool:
        """Ping ``/api/v2/app/version`` on ``self.base_url``.

        Returns True when qB responds with 200 or 403 (403 is what qB
        returns for an unauthenticated /app/version — it still proves the
        endpoint is reachable)."""
        try:
            resp = self.session.get(
                f"{self.base_url}/api/v2/app/version",
                **self._request_kwargs(),
            )
        except requests.RequestException as exc:
            logger.warning(
                f"qBittorrent /app/version ping failed at "
                f"{mask_ip_address(self.base_url)}: {exc}"
            )
            return False
        return resp.status_code in (200, 403)

    def get_existing_hashes(
        self,
        exclude_states: Iterable[str] = ("error", "missingFiles"),
    ) -> set:
        """Return a ``set`` of lowercased torrent hashes currently in the
        client, excluding anything whose state is listed in
        ``exclude_states`` (default: error / missingFiles).

        On HTTP failure we log a warning and return an empty set, matching
        the previous qb_uploader behaviour.
        """
        exclude = set(exclude_states or ())
        try:
            resp = self.session.get(
                f"{self.base_url}/api/v2/torrents/info",
                **self._request_kwargs(),
            )
        except requests.RequestException as exc:
            logger.error(f"Error getting torrent list: {exc}")
            return set()

        if resp.status_code != 200:
            logger.warning(f"Failed to get torrent list: {resp.status_code}")
            return set()

        hashes: set = set()
        for t in resp.json():
            if t.get("state", "") in exclude:
                continue
            h = (t.get("hash") or "").lower()
            if h:
                hashes.add(h)
        return hashes

    def add_torrent(
        self,
        magnet_link: str,
        name: Optional[str] = None,
        category: Optional[str] = None,
        save_path: str = "",
        auto_tmm: bool = True,
        skip_checking: bool = False,
        content_layout: str = "Original",
        ratio_limit: str = "-2",
        seeding_time_limit: str = "-2",
        paused: bool = False,
    ) -> bool:
        """Add a torrent via qB's ``/api/v2/torrents/add`` endpoint.

        All knobs are keyword-only and optional to keep the shared
        implementation free of consumer-specific defaults — callers
        (e.g. qb_uploader) wire the global config values in themselves.

        Returns True on HTTP 200, False otherwise. Network exceptions
        propagate so callers can decide how to log them.
        """
        data = {
            "urls": magnet_link,
            "autoTMM": "true" if auto_tmm else "false",
            "savepath": save_path,
            "downloadPath": "",
            "skip_checking": str(bool(skip_checking)).lower(),
            "contentLayout": content_layout,
            "ratioLimit": ratio_limit,
            "seedingTimeLimit": seeding_time_limit,
            # qBittorrent Web API uses "paused" (not "addPaused") on
            # /api/v2/torrents/add. On qB v5.1.0+ the server-side semantics
            # were reworked around "is_stopped"; see issue #22766. Revisit
            # this assignment if we bump the minimum supported qB version.
            "paused": "true" if paused else "false",
        }
        if name is not None:
            # /api/v2/torrents/add names the override field "rename"; "name"
            # is ignored by qB and has no effect on the resulting torrent.
            data["rename"] = name
        if category is not None:
            data["category"] = category

        resp = self.session.post(
            f"{self.base_url}/api/v2/torrents/add",
            data=data,
            **self._request_kwargs(),
        )
        if resp.status_code == 200:
            return True
        logger.error(
            f"Failed to add torrent (category={category}): "
            f"status {resp.status_code}"
        )
        return False

    def get_torrents_multiple_categories(
        self, categories: Iterable[str], torrent_filter: str = "downloading"
    ) -> list:
        """Fetch torrents for each category; failures for one category
        don't abort the others."""
        all_torrents: list = []
        for category in categories:
            try:
                torrents = self.get_torrents(category, torrent_filter=torrent_filter)
                logger.debug(
                    f"Found {len(torrents)} torrents in category "
                    f"'{category}' (filter={torrent_filter})"
                )
                all_torrents.extend(torrents)
            except Exception as e:
                logger.warning(
                    f"Failed to get torrents from category '{category}': {e}"
                )
        return all_torrents

    def delete_torrents(self, hashes: Iterable[str], delete_files: bool = True) -> bool:
        """Delete the given torrents.

        ``delete_files=False`` removes the torrent entry but preserves
        the content files on disk (useful after the content has been
        handed off to another system such as PikPak)."""
        hash_list = [h for h in hashes if h]
        if not hash_list:
            return True
        resp = self.session.post(
            f"{self.base_url}/api/v2/torrents/delete",
            data={
                "hashes": "|".join(hash_list),
                "deleteFiles": "true" if delete_files else "false",
            },
            **self._request_kwargs(),
        )
        resp.raise_for_status()
        logger.info(
            f"Deleted {len(hash_list)} torrents from qBittorrent "
            f"(delete_files={delete_files})."
        )
        return True


def remove_completed_torrents_keep_files(
    qb_client: QBittorrentClient,
    categories: Iterable[str],
    dry_run: bool = False,
    qb_label: str = "qBittorrent",
) -> dict:
    """Remove completed (seeding / paused-completed) torrents for the given
    categories, preserving the files on disk.

    Returns a small stats dict so callers can log/act on it:
        {"scanned": int, "deleted": int, "hashes": list[str]}
    """
    category_list = list(categories) if categories is not None else []
    if not category_list:
        logger.info(f"{qb_label}: no categories provided — skipping cleanup.")
        return {"scanned": 0, "deleted": 0, "hashes": []}

    completed = qb_client.get_torrents_multiple_categories(
        category_list, torrent_filter="completed"
    )
    if not completed:
        logger.info(
            f"{qb_label}: no completed torrents to remove in categories "
            f"{category_list}."
        )
        return {"scanned": 0, "deleted": 0, "hashes": []}

    hashes = [t["hash"] for t in completed if t.get("hash")]
    if dry_run:
        sample = ", ".join(t.get("name", "") for t in completed[:5])
        extra = " …" if len(completed) > 5 else ""
        logger.info(
            f"[Dry-Run] Would remove {len(hashes)} completed torrent(s) "
            f"from {qb_label} in categories {category_list} "
            f"(torrent only, files kept). Sample: {sample}{extra}"
        )
        return {"scanned": len(completed), "deleted": 0, "hashes": hashes}

    qb_client.delete_torrents(hashes, delete_files=False)
    logger.info(
        f"{qb_label}: removed {len(hashes)} completed torrent(s) from "
        f"client in categories {category_list} (files kept on disk)."
    )
    return {"scanned": len(completed), "deleted": len(hashes), "hashes": hashes}


# ---------------------------------------------------------------------------
# Magnet-link helpers
#
# These are qB-semantics utilities (qB identifies torrents by info-hash), not
# uploader-specific logic, so they belong next to the client.
# ---------------------------------------------------------------------------


_MAGNET_HASH_RE = None  # compiled lazily so ``import re`` only happens once


def extract_hash_from_magnet(magnet_link: str) -> Optional[str]:
    """Extract the BitTorrent info-hash from a magnet URI.

    Returns a lowercased 40-char hex hash on success, ``None`` if the
    magnet does not embed an ``xt=urn:btih:…`` parameter. Accepts both
    40-char hex and 32-char base32 forms (the latter is converted to hex).
    """
    if not magnet_link:
        return None

    import re

    global _MAGNET_HASH_RE
    if _MAGNET_HASH_RE is None:
        _MAGNET_HASH_RE = re.compile(
            r"xt=urn:btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})"
        )

    match = _MAGNET_HASH_RE.search(magnet_link)
    if not match:
        return None

    hash_value = match.group(1)
    if len(hash_value) == 32:
        try:
            import base64
            decoded = base64.b32decode(hash_value.upper())
            hash_value = decoded.hex()
        except Exception:
            pass
    return hash_value.lower()


def is_torrent_exists(magnet_link: str, existing_hashes: Iterable[str]) -> bool:
    """Return True if the torrent referenced by ``magnet_link`` is already
    present in ``existing_hashes`` (typically the set returned by
    :meth:`QBittorrentClient.get_existing_hashes`).

    The magnet's info-hash is compared case-insensitively against the
    given collection. ``existing_hashes`` may be any ``Iterable[str]``
    (set, list, generator…); it is normalised to a lowercased ``set`` so
    membership lookup is O(1) and case-insensitive, and generators are
    consumed exactly once.
    """
    torrent_hash = extract_hash_from_magnet(magnet_link)
    if not torrent_hash:
        return False
    if existing_hashes is None:
        return False
    normalised = {
        h.lower() for h in existing_hashes if isinstance(h, str) and h
    }
    return torrent_hash.lower() in normalised


__all__ = [
    "QBittorrentClient",
    "remove_completed_torrents_keep_files",
    "try_ping_base_urls",
    "try_login_base_urls",
    "LOGIN_SUCCESS",
    "LOGIN_REJECTED",
    "LOGIN_UNREACHABLE",
    "extract_hash_from_magnet",
    "is_torrent_exists",
]
