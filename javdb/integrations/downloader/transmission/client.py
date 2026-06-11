"""Thin requests-based JSON-RPC client for Transmission (ADR-039 Phase 2).

Implements the 409 X-Transmission-Session-Id negotiation:
  - POST to /transmission/rpc
  - If 409, extract X-Transmission-Session-Id from response headers, retry once
  - Subsequent calls reuse the stored session-id
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30


class TransmissionRpcClient:
    """Minimal Transmission JSON-RPC client.

    Only exposes torrent-add. Raises on network errors so the plugin layer
    can catch them and surface a DownloadResult(ok=False).
    """

    def __init__(
        self,
        base_url: str,
        username: str = "",
        password: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        # base_url should be e.g. 'http://192.168.1.10:9091'
        self._rpc_url = base_url.rstrip("/") + "/transmission/rpc"
        self._auth = HTTPBasicAuth(username, password) if username else None
        self._timeout = timeout
        self._session_id: str = ""

    def _post(self, payload: dict) -> dict:
        """POST JSON-RPC payload; handles the 409 session-id negotiation."""
        headers = {"X-Transmission-Session-Id": self._session_id, "Content-Type": "application/json"}
        resp = requests.post(
            self._rpc_url,
            json=payload,
            headers=headers,
            auth=self._auth,
            timeout=self._timeout,
        )
        if resp.status_code == 409:
            # Transmission rejected the request and provided a fresh session-id.
            self._session_id = resp.headers.get("X-Transmission-Session-Id", "")
            logger.debug("Transmission session-id updated to %r", self._session_id)
            # Retry once with the new session-id.
            headers["X-Transmission-Session-Id"] = self._session_id
            resp = requests.post(
                self._rpc_url,
                json=payload,
                headers=headers,
                auth=self._auth,
                timeout=self._timeout,
            )
        resp.raise_for_status()
        return resp.json()

    def torrent_add(
        self,
        magnet: str,
        download_dir: str,
        labels: Optional[List[str]] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Add a torrent via the torrent-add RPC method.

        Returns (True, None) on success, (False, detail) on RPC-level failure.
        Raises on network errors (caller handles).
        """
        payload = {
            "method": "torrent-add",
            "arguments": {
                "filename": magnet,
                "download-dir": download_dir,
                "labels": labels or [],
            },
        }
        result = self._post(payload)
        rpc_result = result.get("result", "")
        if rpc_result == "success":
            return True, None
        detail = f"rpc result: {rpc_result}"
        logger.warning("Transmission torrent-add failed: %s", detail)
        return False, detail
