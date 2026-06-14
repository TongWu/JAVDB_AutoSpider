"""Explore and one-click API services."""

from __future__ import annotations

import asyncio
import csv
import json
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from bs4 import BeautifulSoup
from fastapi import HTTPException
from fastapi.responses import HTMLResponse

from apps.api.infra.security import _resolve_public_target_or_422
from javdb.parsing import detect_page_type, parse_detail_page, parse_index_page
from apps.api.services import config_service, context
from javdb.integrations.qb.client import (
    LOGIN_REJECTED,
    LOGIN_SUCCESS,
    QBittorrentClient,
    try_login_base_urls,
)
from javdb.integrations.qb.config import qb_base_url_candidates, qb_verify_tls
from apps.api.services.javdb_fetch_service import (
    fetch_javdb_html as _fetch_javdb_html,
    validate_javdb_url_or_422 as _validate_javdb_url_or_422,
)

EXPLORE_DETAIL_CACHE: Dict[str, tuple[int, bool]] = {}
_DOWNLOADED_MAP_CACHE: Dict[str, tuple[float, Dict[str, bool]]] = {}
_MAX_DOWNLOADED_MAP_CACHE_SIZE = 8


def _sanitize_proxied_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in list(soup.find_all(True)):
        tag_name = str(tag.name or "").lower()
        if tag_name in {"base", "embed", "frame", "iframe", "noscript", "object", "script"}:
            tag.decompose()
            continue
        if tag_name == "meta" and str(tag.get("http-equiv", "")).strip():
            tag.decompose()
            continue
        for attr_name in list(tag.attrs.keys()):
            lowered = attr_name.lower()
            if lowered.startswith("on") or lowered == "srcdoc":
                del tag.attrs[attr_name]
                continue
            if lowered in {"action", "formaction", "href", "src"}:
                raw_value = tag.attrs.get(attr_name)
                values = raw_value if isinstance(raw_value, list) else [raw_value]
                if any(
                    str(value).strip().lower().startswith(
                        ("data:text/html", "javascript:", "vbscript:")
                    )
                    for value in values
                ):
                    del tag.attrs[attr_name]
    return str(soup)


def _pick_best_magnet(magnets: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not magnets:
        return None
    preferred_tokens = ("中字", "字幕", "破解", "uncensored", "無碼", "无码")

    def _score(item: dict[str, Any]) -> tuple[int, int, int]:
        tags = [str(x) for x in item.get("tags", [])]
        name = str(item.get("name", ""))
        score = 0
        for token in preferred_tokens:
            if token in name or any(token in tag for tag in tags):
                score += 3
        if "高清" in name or "1080" in name:
            score += 2
        size_hint = str(item.get("size", "")).upper()
        if "GB" in size_hint:
            score += 1
        return (score, int(item.get("file_count", 0) or 0), len(name))

    return sorted(magnets, key=_score, reverse=True)[0]


def _qb_configured_base_urls(cfg: Dict[str, Any]) -> list[str]:
    qb_url = str(cfg.get("QB_URL", "")).strip()
    qb_url_default = str(
        context.CONFIG_SCHEMA.get("QB_URL", {}).get("default", "") or ""
    ).strip()
    host = str(cfg.get("QB_HOST", "")).strip()
    port = str(cfg.get("QB_PORT", "")).strip()
    allow_insecure_http = cfg.get("QB_ALLOW_INSECURE_HTTP", False)
    if qb_url and not (host and port and qb_url == qb_url_default):
        try:
            return qb_base_url_candidates(
                qb_url,
                allow_insecure_http=allow_insecure_http,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="Invalid qBittorrent transport settings",
            ) from exc
    if not host or not port:
        raise HTTPException(status_code=422, detail="qBittorrent config is incomplete")
    try:
        return qb_base_url_candidates(
            host,
            port,
            scheme=cfg.get("QB_SCHEME", "https"),
            allow_insecure_http=allow_insecure_http,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="Invalid qBittorrent transport settings",
        ) from exc


def _qb_login_session(cfg: Dict[str, Any]) -> tuple[requests.Session, str]:
    username = str(cfg.get("QB_USERNAME", "")).strip()
    password = str(cfg.get("QB_PASSWORD", "")).strip()
    if not username or not password:
        raise HTTPException(status_code=422, detail="qBittorrent config is incomplete")
    base_urls = _qb_configured_base_urls(cfg)
    verify_tls = qb_verify_tls(cfg.get("QB_VERIFY_TLS", True))
    session = requests.Session()
    session.verify = verify_tls
    timeout = int(cfg.get("REQUEST_TIMEOUT", 30) or 30)
    outcome, login_url, _ = try_login_base_urls(
        base_urls,
        username,
        password,
        post_fn=session.post,
        timeout=timeout,
        verify=verify_tls,
    )
    if outcome == LOGIN_SUCCESS and login_url:
        return session, login_url
    if outcome == LOGIN_REJECTED:
        raise HTTPException(
            status_code=502,
            detail="Failed to login qBittorrent: credentials rejected",
        )
    raise HTTPException(status_code=502, detail="Failed to login qBittorrent")


def _qb_add_magnet(
    cfg: Dict[str, Any],
    magnet: str,
    title: str,
    category: Optional[str] = None,
) -> None:
    verify_tls = qb_verify_tls(cfg.get("QB_VERIFY_TLS", True))
    session, base_url = _qb_login_session(cfg)
    effective_category = category or str(
        cfg.get("TORRENT_CATEGORY_ADHOC", "") or cfg.get("TORRENT_CATEGORY", "")
    )
    # Reuse the canonical client instead of re-POSTing /torrents/add by hand: the
    # hand-rolled payload had drifted (it sent "addPaused"/"name", which qB
    # ignores — the API field names are "paused"/"rename"; see BFR-018).
    client = QBittorrentClient.from_existing_session(
        session, base_url, request_timeout=int(cfg.get("REQUEST_TIMEOUT", 30) or 30)
    )
    # from_existing_session aligns session.verify with the *global* qb_verify_tls();
    # re-assert this request's QB_VERIFY_TLS so per-request transport settings win.
    client.session.verify = verify_tls
    added = client.add_torrent(
        magnet,
        name=title,
        category=effective_category,
        save_path=str(cfg.get("TORRENT_SAVE_PATH", "") or ""),
        auto_tmm=True,
        skip_checking=bool(cfg.get("SKIP_CHECKING", False)),
        paused=not bool(cfg.get("AUTO_START", True)),
    )
    if not added:
        raise HTTPException(status_code=502, detail="Failed to add magnet to qBittorrent")


def _resolved_history_csv_path(cfg: Dict[str, Any]) -> Path:
    reports_dir_raw = str(cfg.get("REPORTS_DIR", "reports") or "reports").strip()
    history_raw = str(
        cfg.get("PARSED_MOVIES_CSV", "parsed_movies_history.csv")
        or "parsed_movies_history.csv"
    ).strip()

    def _resolve_under_root(raw: str, field: str) -> Path:
        path = Path(raw)
        if path.is_absolute():
            raise HTTPException(status_code=422, detail=f"{field} must be a relative path")
        candidate = (context.REPO_ROOT / path).resolve()
        try:
            candidate.relative_to(context.REPO_ROOT.resolve())
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"{field} escapes project root",
            ) from exc
        return candidate

    def _safe_single_segment(name: str, field: str) -> str:
        if not name:
            raise HTTPException(status_code=422, detail=f"{field} cannot be empty")
        if "/" in name or "\\" in name:
            raise HTTPException(status_code=422, detail=f"{field} must be a file name")
        segment = Path(name)
        if segment.is_absolute() or len(segment.parts) != 1 or name in {".", ".."}:
            raise HTTPException(status_code=422, detail=f"{field} is invalid")
        if ".." in name:
            raise HTTPException(
                status_code=422,
                detail=f"{field} cannot contain traversal markers",
            )
        return name

    reports_dir = _resolve_under_root(reports_dir_raw or "reports", "REPORTS_DIR")
    if "/" in history_raw or "\\" in history_raw:
        return _resolve_under_root(history_raw, "PARSED_MOVIES_CSV")
    history_name = _safe_single_segment(
        history_raw or "parsed_movies_history.csv",
        "PARSED_MOVIES_CSV",
    )
    candidate = (reports_dir / history_name).resolve()
    try:
        candidate.relative_to(context.REPO_ROOT.resolve())
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="PARSED_MOVIES_CSV escapes project root",
        ) from exc
    return candidate


def _downloaded_map_by_href(cfg: Dict[str, Any]) -> Dict[str, bool]:
    history_path = _resolved_history_csv_path(cfg)
    cache_key = str(history_path)
    now = time.time()
    cached = _DOWNLOADED_MAP_CACHE.get(cache_key)
    if cached is not None:
        ts, data = cached
        if (now - ts) < context.EXPLORE_DOWNLOADED_MAP_CACHE_TTL_SECONDS:
            return data

    downloaded: Dict[str, bool] = {}
    if not history_path.exists():
        _DOWNLOADED_MAP_CACHE[cache_key] = (now, downloaded)
        return downloaded
    try:
        with open(history_path, "r", encoding="utf-8-sig") as fp:
            reader = csv.DictReader(fp)
            for row in reader:
                href = str(row.get("href", "")).strip()
                if not href:
                    continue
                downloaded[href] = True
    except Exception:
        return {}
    _DOWNLOADED_MAP_CACHE[cache_key] = (now, downloaded)
    if len(_DOWNLOADED_MAP_CACHE) > _MAX_DOWNLOADED_MAP_CACHE_SIZE:
        oldest_key = min(_DOWNLOADED_MAP_CACHE, key=lambda k: _DOWNLOADED_MAP_CACHE[k][0])
        _DOWNLOADED_MAP_CACHE.pop(oldest_key, None)
    return downloaded


def _inject_explore_enhancer(html: str, source_url: str, *, nonce: str = "") -> str:
    raw = str(source_url)
    safe_parts: list[str] = []
    for ch in raw:
        if ch in "<>":
            continue
        codepoint = ord(ch)
        if codepoint < 32 or codepoint == 127:
            continue
        safe_parts.append(ch)
    escaped_url = json.dumps("".join(safe_parts))
    nonce_attr = f' nonce="{nonce}"' if nonce else ""
    enhancer = f"""
<script{nonce_attr}>
(function() {{
  const SOURCE_URL = {escaped_url};
  const authHeaders = {{ "Content-Type": "application/json" }};

  function abs(href) {{
    try {{ return new URL(href, SOURCE_URL).toString(); }} catch (e) {{ return href; }}
  }}
  function isDetail(url) {{
    try {{ return /^\\/v\\//.test(new URL(url).pathname); }} catch (e) {{ return false; }}
  }}
  function isJavdb(url) {{
    try {{ return /(^|\\.)javdb\\.com$/i.test(new URL(url).hostname); }} catch (e) {{ return false; }}
  }}
  function postToParent(type, payload) {{
    window.parent && window.parent.postMessage(Object.assign({{ type }}, payload || {{}}), "*");
  }}
  function notifyUrl() {{
    postToParent("explore:url", {{ url: SOURCE_URL }});
  }}
  function linkToProxy(url) {{
    const u = new URL("/api/explore/proxy-page", window.location.origin);
    u.searchParams.set("url", url);
    return u.toString();
  }}
  function patchLinks() {{
    document.querySelectorAll("a[href]").forEach((a) => {{
      const href = a.getAttribute("href");
      if (!href || href.startsWith("#") || href.startsWith("javascript:")) return;
      const target = abs(href);
      if (!isJavdb(target)) return;
      a.setAttribute("href", linkToProxy(target));
    }});
  }}
  async function apiPost(path, payload) {{
    const res = await fetch(path, {{
      method: "POST",
      headers: authHeaders,
      credentials: "include",
      body: JSON.stringify(payload || {{}})
    }});
    if (!res.ok) throw new Error("HTTP " + res.status);
    return res.json().catch(() => ({{}}));
  }}
  function mkBtn(text) {{
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = text;
    b.style.marginLeft = "8px";
    b.style.padding = "3px 8px";
    b.style.fontSize = "12px";
    b.style.border = "1px solid #ccc";
    b.style.borderRadius = "4px";
    b.style.cursor = "pointer";
    b.style.background = "#fff";
    return b;
  }}
  function addAdhocJump() {{
    if (!isJavdb(SOURCE_URL) || isDetail(SOURCE_URL)) return;
    const bar = document.querySelector(".tabs") || document.body;
    const btn = mkBtn("跳转至adhoc任务创建");
    btn.addEventListener("click", () => postToParent("explore:jump-adhoc", {{ url: SOURCE_URL }}));
    bar.appendChild(btn);
  }}
  function addDetailButtons() {{
    if (!isDetail(SOURCE_URL)) return;
    const rows = document.querySelectorAll("#magnets-content .item.columns.is-desktop");
    rows.forEach((row) => {{
      const anchor = row.querySelector(".magnet-name a[href^='magnet:']");
      if (!anchor || row.querySelector(".explore-qb-btn")) return;
      const btn = mkBtn("使用qBittorrent下载");
      btn.className = "explore-qb-btn";
      btn.addEventListener("click", async () => {{
        try {{
          await apiPost("/api/explore/download-magnet", {{
            magnet: anchor.getAttribute("href") || "",
            title: (anchor.querySelector(".name") && anchor.querySelector(".name").textContent || "").trim()
          }});
          btn.textContent = "已提交";
        }} catch (e) {{
          btn.textContent = "失败";
        }}
      }});
      anchor.parentElement && anchor.parentElement.appendChild(btn);
    }});
    const host = document.querySelector("#magnets-content");
    if (host && !document.querySelector(".explore-one-click-btn")) {{
      const one = mkBtn("一键下载");
      one.className = "explore-one-click-btn";
      one.style.margin = "8px 0";
      one.addEventListener("click", async () => {{
        try {{
          await apiPost("/api/explore/one-click", {{ detail_url: SOURCE_URL, use_proxy: false, use_cookie: true }});
          one.textContent = "已提交";
        }} catch (e) {{
          one.textContent = "失败";
        }}
      }});
      host.parentElement && host.parentElement.insertBefore(one, host);
    }}
  }}
  function addIndexButtons() {{
    const cards = document.querySelectorAll(".movie-list .item a.box[href]");
    if (!cards.length) return;
    cards.forEach((a) => {{
      if (a.querySelector(".explore-card-oneclick")) return;
      const btn = mkBtn("一键下载");
      btn.className = "explore-card-oneclick";
      btn.addEventListener("click", async (e) => {{
        e.preventDefault();
        e.stopPropagation();
        try {{
          await apiPost("/api/explore/one-click", {{ detail_url: abs(a.getAttribute("href") || ""), use_proxy: false, use_cookie: true }});
          btn.textContent = "已提交";
        }} catch (err) {{
          btn.textContent = "失败";
        }}
      }});
      const title = a.querySelector(".video-title");
      if (title && title.parentElement) title.parentElement.appendChild(btn);
    }});
    if (!document.querySelector(".explore-page-oneclick")) {{
      const pageBtn = mkBtn("整页一键下载");
      pageBtn.className = "explore-page-oneclick";
      pageBtn.style.margin = "8px 0";
      pageBtn.addEventListener("click", async () => {{
        for (const a of cards) {{
          try {{
            await apiPost("/api/explore/one-click", {{ detail_url: abs(a.getAttribute("href") || ""), use_proxy: false, use_cookie: true }});
          }} catch (e) {{}}
        }}
        pageBtn.textContent = "已提交";
      }});
      const mount = document.querySelector(".movie-list") || document.body;
      mount.parentElement && mount.parentElement.insertBefore(pageBtn, mount);
    }}
  }}
  async function addIndexStatusTags() {{
    const cards = Array.from(document.querySelectorAll(".movie-list .item a.box[href]"));
    if (!cards.length) return;
    const movies = cards.map((a) => {{
      const href = abs(a.getAttribute("href") || "");
      const codeNode = a.querySelector(".uid");
      return {{ href, video_code: (codeNode && codeNode.textContent || "").trim() }};
    }});
    try {{
      const data = await apiPost("/api/explore/index-status", {{ movies, use_proxy: false, use_cookie: true }});
      const items = data.items || {{}};
      cards.forEach((a) => {{
        const href = abs(a.getAttribute("href") || "");
        const st = items[href];
        if (!st) return;
        if (a.querySelector(".explore-status-row")) return;
        const row = document.createElement("div");
        row.className = "explore-status-row";
        row.style.marginTop = "4px";
        row.style.fontSize = "11px";
        row.style.color = "#666";
        row.textContent = (st.has_uncensored ? "有无码" : "无码未知/无") + " · " + (st.downloaded ? "已下载" : "未下载");
        const title = a.querySelector(".video-title");
        if (title && title.parentElement) title.parentElement.appendChild(row);
      }});
    }} catch (e) {{}}
  }}
  function init() {{
    notifyUrl();
    patchLinks();
    addAdhocJump();
    addDetailButtons();
    addIndexButtons();
    addIndexStatusTags();
  }}
  if (document.readyState === "loading") {{
    document.addEventListener("DOMContentLoaded", init);
  }} else {{
    init();
  }}
}})();
</script>
"""
    if "</body>" in html:
        return html.replace("</body>", enhancer + "</body>")
    return html + enhancer


def _has_uncensored_magnet(
    absolute_url: str,
    use_proxy: bool,
    use_cookie: bool,
) -> bool:
    now = int(time.time())
    cached = EXPLORE_DETAIL_CACHE.get(absolute_url)
    if cached and (now - cached[0]) < context.EXPLORE_INDEX_STATUS_CACHE_TTL_SECONDS:
        return cached[1]

    has_uncensored = False
    try:
        html = _fetch_javdb_html(
            absolute_url,
            use_proxy=use_proxy,
            use_cookie=use_cookie,
        )
        detail = parse_detail_page(html).to_dict()
        magnets = detail.get("magnets", [])
        if isinstance(magnets, list):
            for magnet in magnets:
                tags = (
                    [str(x) for x in magnet.get("tags", [])]
                    if isinstance(magnet, dict)
                    else []
                )
                name = str(magnet.get("name", "")) if isinstance(magnet, dict) else ""
                if any(
                    token in name for token in ("無碼", "无码", "uncensored")
                ) or any(
                    any(token in tag for token in ("無碼", "无码", "uncensored"))
                    for tag in tags
                ):
                    has_uncensored = True
                    break
    except Exception:
        has_uncensored = False

    EXPLORE_DETAIL_CACHE[absolute_url] = (now, has_uncensored)
    if len(EXPLORE_DETAIL_CACHE) > context.EXPLORE_INDEX_STATUS_CACHE_MAX_ITEMS:
        stale = [
            url
            for url, (ts, _) in EXPLORE_DETAIL_CACHE.items()
            if (now - ts) >= context.EXPLORE_INDEX_STATUS_CACHE_TTL_SECONDS
        ]
        for url in stale:
            EXPLORE_DETAIL_CACHE.pop(url, None)
    return has_uncensored


def sync_cookie_payload(cookie: str, username: str) -> Dict[str, str]:
    return config_service.set_javdb_session_cookie(cookie, username)


async def proxy_page_payload(url: str, username: str) -> HTMLResponse:
    _validate_javdb_url_or_422(url)
    try:
        html = _fetch_javdb_html(url, use_proxy=False, use_cookie=True)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Failed to fetch target page",
        ) from exc

    nonce = secrets.token_urlsafe(16)
    sanitized_html = _sanitize_proxied_html(html)
    injected = _inject_explore_enhancer(sanitized_html, url, nonce=nonce)
    context.audit_logger.info("explore_proxy_page username=%s", username)
    csp = (
        "default-src 'none'; "
        f"script-src 'nonce-{nonce}'; "
        "style-src 'unsafe-inline' https:; "
        "img-src https: data:; "
        "font-src https: data:; "
        "connect-src 'self'; "
        "media-src https: data:; "
        "frame-ancestors 'self'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "form-action 'none'"
    )
    return HTMLResponse(
        content=injected,
        headers={
            "Content-Security-Policy": csp,
            "X-Content-Type-Options": "nosniff",
        },
    )


async def resolve_payload(payload: Any, username: str) -> Dict[str, Any]:
    _validate_javdb_url_or_422(payload.url)
    html = _fetch_javdb_html(
        payload.url,
        use_proxy=payload.use_proxy,
        use_cookie=payload.use_cookie,
    )
    page_type = detect_page_type(html)
    body: Dict[str, Any] = {
        "url": payload.url,
        "page_type": page_type,
    }
    if page_type == "detail":
        body["detail"] = parse_detail_page(html).to_dict()
    else:
        body["index"] = parse_index_page(html, payload.page_num).to_dict()
    context.audit_logger.info(
        "explore_resolve username=%s page_type=%s",
        username,
        page_type,
    )
    return body


async def download_magnet_payload(payload: Any, username: str) -> Dict[str, str]:
    cfg = config_service.load_runtime_config()
    _qb_add_magnet(cfg, payload.magnet, payload.title or "JavDB", payload.category)
    context.audit_logger.info("explore_download_magnet username=%s", username)
    return {"status": "ok"}


async def one_click_payload(payload: Any, username: str) -> Dict[str, Any]:
    _validate_javdb_url_or_422(payload.detail_url)
    html = _fetch_javdb_html(
        payload.detail_url,
        use_proxy=payload.use_proxy,
        use_cookie=payload.use_cookie,
    )
    detail = parse_detail_page(html).to_dict()
    magnets = detail.get("magnets", [])
    best = _pick_best_magnet(magnets if isinstance(magnets, list) else [])
    if not best:
        raise HTTPException(status_code=404, detail="No magnet found in detail page")
    cfg = config_service.load_runtime_config()
    title = str(best.get("name") or detail.get("video_code") or "JavDB")
    _qb_add_magnet(cfg, str(best.get("href", "")), title, payload.category)
    context.audit_logger.info("explore_one_click username=%s", username)
    return {
        "status": "ok",
        "selected": best,
        "video_code": detail.get("video_code", ""),
    }


async def index_status_payload(payload: Any, username: str) -> Dict[str, Any]:
    cfg = config_service.load_runtime_config()
    downloaded_map = _downloaded_map_by_href(cfg)
    statuses: Dict[str, Dict[str, Any]] = {}
    candidates: list[tuple[str, str]] = []
    for item in payload.movies[: context.EXPLORE_INDEX_STATUS_MAX_ITEMS]:
        href = str(item.get("href", "")).strip()
        if not href:
            continue
        absolute_url = href if href.startswith("http") else f"https://javdb.com{href}"
        try:
            _validate_javdb_url_or_422(absolute_url)
        except HTTPException:
            continue
        is_downloaded = bool(downloaded_map.get(href) or downloaded_map.get(absolute_url))
        statuses[href] = {
            "downloaded": is_downloaded,
            "has_uncensored": False,
        }
        candidates.append((href, absolute_url))

    semaphore = asyncio.Semaphore(context.EXPLORE_INDEX_STATUS_CONCURRENCY)

    async def _fetch_status(target_href: str, target_url: str) -> tuple[str, bool]:
        async with semaphore:
            try:
                has_uncensored = await asyncio.wait_for(
                    asyncio.to_thread(
                        _has_uncensored_magnet,
                        target_url,
                        payload.use_proxy,
                        payload.use_cookie,
                    ),
                    timeout=context.EXPLORE_INDEX_STATUS_ITEM_TIMEOUT_SECONDS,
                )
            except Exception:
                has_uncensored = False
            return target_href, has_uncensored

    tasks = [asyncio.create_task(_fetch_status(href, url)) for href, url in candidates]
    done_count = 0
    timeout_count = 0
    if tasks:
        done, pending = await asyncio.wait(
            tasks,
            timeout=context.EXPLORE_INDEX_STATUS_TOTAL_TIMEOUT_SECONDS,
        )
        for task in done:
            try:
                href, has_uncensored = task.result()
            except Exception:
                continue
            if href in statuses:
                statuses[href]["has_uncensored"] = has_uncensored
                done_count += 1
        timeout_count = len(pending)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    context.audit_logger.info(
        "explore_index_status username=%s count=%s done=%s timeout=%s",
        username,
        len(statuses),
        done_count,
        timeout_count,
    )
    return {"items": statuses}


__all__ = [
    "_DOWNLOADED_MAP_CACHE",
    "_downloaded_map_by_href",
    "_fetch_javdb_html",
    "_has_uncensored_magnet",
    "_inject_explore_enhancer",
    "_pick_best_magnet",
    "_qb_add_magnet",
    "_qb_login_session",
    "_resolve_public_target_or_422",
    "_resolved_history_csv_path",
    "_validate_javdb_url_or_422",
    "download_magnet_payload",
    "index_status_payload",
    "one_click_payload",
    "proxy_page_payload",
    "resolve_payload",
    "sync_cookie_payload",
]
