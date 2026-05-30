"""In-memory qBittorrent fake with controllable torrent state (ADR-037 D4).

Implements the ``QBittorrentClient`` surface the uploader actually uses
(``add_torrent``, ``get_existing_hashes``, ``get_torrents_multiple_categories``,
``delete_torrents``) over an in-memory dict, computing the hash from the magnet
the same way production does (``extract_hash_from_magnet``) so that
add -> get_existing_hashes -> AcquisitionOutcome line up. Control methods
(``complete``/``stall``) let a scenario simulate a download finishing."""

from __future__ import annotations

from javdb.integrations.qb.client import extract_hash_from_magnet


class FakeQB:
    def __init__(self, *, fail_adds: bool = False) -> None:
        # hash -> {hash, name, category, state, progress}
        self._torrents: dict[str, dict] = {}
        # When True, every add_torrent is rejected — lets a scenario simulate a
        # qB that accepts the connection/login but refuses every magnet, so
        # run_uploader reports a nonzero exit_code (all adds failed).
        self._fail_adds = fail_adds

    # --- QBittorrentClient surface used by the pipeline -------------------
    def add_torrent(self, magnet_link, name=None, category=None, **_kw) -> bool:
        if self._fail_adds:
            return False
        h = extract_hash_from_magnet(magnet_link)
        if not h:
            return False
        self._torrents[h] = {"hash": h, "name": name or h, "category": category or "",
                             "state": "downloading", "progress": 0.0}
        return True

    def get_existing_hashes(self):
        return set(self._torrents.keys())

    def get_torrents_multiple_categories(self, categories, torrent_filter="downloading"):
        cats = set(categories)
        out = []
        for t in self._torrents.values():
            if t["category"] not in cats:
                continue
            done = t["progress"] >= 1.0
            if torrent_filter == "completed" and not done:
                continue
            if torrent_filter == "downloading" and done:
                continue
            out.append(dict(t))
        return out

    def delete_torrents(self, hashes, delete_files=True) -> bool:
        for h in hashes:
            self._torrents.pop(h, None)
        return True

    # --- control surface -------------------------------------------------
    def complete(self, qb_hash: str) -> None:
        if qb_hash in self._torrents:
            self._torrents[qb_hash]["progress"] = 1.0
            self._torrents[qb_hash]["state"] = "uploading"

    def stall(self, qb_hash: str) -> None:
        if qb_hash in self._torrents:
            self._torrents[qb_hash]["state"] = "stalledDL"

    def all_hashes(self) -> set:
        return set(self._torrents.keys())
