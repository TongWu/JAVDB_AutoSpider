"""On-disk cassette load/save for the pipeline harness (ADR-037 D3, Phase 2).

A cassette is a directory: ``manifest.json`` maps each request URL to a body
file, with one ``NNNN.html`` per page. Only the response *body* is stored —
``RequestHandler.get_page``'s contract is ``str | None`` (the HTML body), so
status/headers are not part of this seam."""

from __future__ import annotations

import json
import os

_MANIFEST = "manifest.json"


def save_cassette(cassette_dir: str, pages: dict[str, str]) -> None:
    """Write ``pages`` (URL -> HTML body) to ``cassette_dir`` as a cassette.

    Pages are sorted for a stable, diff-friendly manifest/body layout."""
    os.makedirs(cassette_dir, exist_ok=True)
    entries = []
    for i, (url, body) in enumerate(sorted(pages.items()), start=1):
        fname = f"{i:04d}.html"
        with open(os.path.join(cassette_dir, fname), "w", encoding="utf-8") as f:
            f.write(body)
        entries.append({"url": url, "file": fname})
    with open(os.path.join(cassette_dir, _MANIFEST), "w", encoding="utf-8") as f:
        json.dump({"pages": entries}, f, ensure_ascii=False, indent=2)


def load_cassette(cassette_dir: str) -> dict[str, str]:
    """Read a cassette directory back into a ``{url: body}`` dict."""
    with open(os.path.join(cassette_dir, _MANIFEST), "r", encoding="utf-8") as f:
        manifest = json.load(f)
    pages: dict[str, str] = {}
    for entry in manifest["pages"]:
        with open(os.path.join(cassette_dir, entry["file"]), "r", encoding="utf-8") as bf:
            pages[entry["url"]] = bf.read()
    return pages
