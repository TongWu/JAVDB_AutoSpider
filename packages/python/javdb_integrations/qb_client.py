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

from typing import Iterable, List, Optional

import requests

from packages.python.javdb_core.masking import mask_ip_address, mask_username
from packages.python.javdb_platform.logging_config import get_logger
from packages.python.javdb_platform.qb_config import qb_verify_tls


logger = get_logger(__name__)


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
        performing a new login. Used by qb_file_filter, which manages
        its own session+login lifecycle but wants to reuse the shared
        API helpers."""
        obj = cls.__new__(cls)
        obj.base_urls = [base_url.rstrip("/")]
        obj.base_url = obj.base_urls[0]
        obj.session = session
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
        last_error: Optional[BaseException] = None
        primary_url = self.base_urls[0]

        for candidate in self.base_urls:
            try:
                resp = self.session.post(
                    f"{candidate}/api/v2/auth/login",
                    data={"username": username, "password": password},
                    **self._request_kwargs(),
                )
            except requests.RequestException as exc:
                last_error = exc
                logger.warning(
                    f"qBittorrent login attempt failed at "
                    f"{mask_ip_address(candidate)}: {exc}"
                )
                continue

            if resp.status_code == 200 and resp.text == "Ok.":
                self.base_url = candidate
                masked_url = mask_ip_address(self.base_url)
                if candidate != primary_url:
                    logger.info(
                        f"qBittorrent HTTPS login failed; retried successfully "
                        f"over HTTP at {masked_url}."
                    )
                logger.info(
                    f"Logged into qBittorrent at {masked_url} as "
                    f"{mask_username(username)} successfully."
                )
                return

            last_error = Exception(resp.text)
            logger.warning(
                f"qBittorrent login failed at {mask_ip_address(candidate)}: "
                f"{resp.text}"
            )

        if last_error is None:
            raise Exception("Failed to login qBittorrent")
        raise Exception(f"Failed to login qBittorrent: {last_error}")

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


__all__ = ["QBittorrentClient", "remove_completed_torrents_keep_files"]
