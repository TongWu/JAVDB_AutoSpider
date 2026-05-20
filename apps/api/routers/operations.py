"""Operations endpoints — qBittorrent, PikPak, Email (Phase 2, Task 3a).

GET  /api/ops/qb/torrents          — list qB torrents (proxied)
POST /api/ops/qb/filter-small      — trigger file filter
GET  /api/ops/pikpak/queue         — PikPak queue from PikpakHistory
POST /api/ops/pikpak/transfer      — batch PikPak transfer
POST /api/ops/email/test           — send test email
GET  /api/ops/email/history        — list EmailNotificationHistory
POST /api/ops/email/{id}/resend    — resend a failed notification

Rclone and Cleanup endpoints (Task 3b) will be added to this router file.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from apps.api.infra.auth import _require_auth, require_role
from apps.api.schemas.operations import (
    EmailHistoryItem,
    EmailHistoryResponse,
    EmailTestRequest,
    PikPakQueueItem,
    PikPakQueueResponse,
    PikPakTransferRequest,
    PikPakTransferResponse,
    QbFilterSmallRequest,
    QbFilterSmallResponse,
    QbTorrentItem,
    QbTorrentsResponse,
)
from javdb.storage.repos.operations_repo import OperationsRepo

router = APIRouter(prefix="/api/ops", tags=["operations"])

_ERR_QB_UNREACHABLE = {"error": {"code": "ops.qb.unreachable", "message": "Cannot connect to qBittorrent"}}
_ERR_QB_AUTH = {"error": {"code": "ops.qb.auth_failed", "message": "Failed to login to qBittorrent"}}
_ERR_PIKPAK_FAILED = {"error": {"code": "ops.pikpak.failed", "message": "PikPak transfer failed"}}
_ERR_EMAIL_SEND = {"error": {"code": "ops.email.send_failed", "message": "Failed to send email"}}
_ERR_EMAIL_NOT_FOUND = {"error": {"code": "ops.email.not_found", "message": "Email history record not found"}}
_ERR_EMAIL_RESEND = {"error": {"code": "ops.email.resend_failed", "message": "Failed to resend email"}}


# ---------------------------------------------------------------------------
# qBittorrent
# ---------------------------------------------------------------------------


@router.get("/qb/torrents", response_model=QbTorrentsResponse)
def list_qb_torrents(
    _user=Depends(_require_auth),
) -> QbTorrentsResponse:
    """List torrents from qBittorrent (proxied through the API)."""
    from javdb.integrations.qb.client import QBittorrentClient
    from javdb.integrations.qb.config import qb_base_url_candidates
    from javdb.infra.config import cfg

    qb_username = cfg("QB_USERNAME", "")
    qb_password = cfg("QB_PASSWORD", "")

    qb = None
    try:
        try:
            qb = QBittorrentClient(
                qb_base_url_candidates(),
                qb_username,
                qb_password,
            )
            raw = qb.get_torrents(torrent_filter="all")
        except Exception as exc:
            raise HTTPException(status_code=502, detail=_ERR_QB_UNREACHABLE) from exc
    finally:
        if qb is not None:
            qb.session.close()

    items = [
        QbTorrentItem(
            hash=t.get("hash", ""),
            name=t.get("name", ""),
            size=int(t.get("size", 0)),
            progress=float(t.get("progress", 0.0)),
            state=t.get("state", ""),
            category=t.get("category", ""),
            added_on=int(t.get("added_on", 0)),
            completion_on=int(t.get("completion_on", 0)),
        )
        for t in raw
    ]
    return QbTorrentsResponse(items=items, total=len(items))


@router.post("/qb/filter-small", response_model=QbFilterSmallResponse)
def qb_filter_small(
    body: QbFilterSmallRequest,
    _user=Depends(require_role("admin")),
) -> QbFilterSmallResponse:
    """Trigger the qBittorrent file-size filter via ``run_file_filter``."""
    from javdb.integrations.qb.file_filter import run_file_filter

    try:
        result = run_file_filter(
            min_size_mb=body.min_size_mb,
            days=body.days,
            dry_run=body.dry_run,
            categories=body.categories,
            delete_local_files=body.delete_local_files,
        )
    except RuntimeError as exc:
        msg = str(exc)
        if "connect" in msg.lower():
            raise HTTPException(status_code=502, detail=_ERR_QB_UNREACHABLE) from exc
        if "login" in msg.lower():
            raise HTTPException(status_code=502, detail=_ERR_QB_AUTH) from exc
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "ops.qb.filter_failed", "message": msg}},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "ops.qb.filter_failed", "message": str(exc)}},
        ) from exc

    return QbFilterSmallResponse(
        filtered_count=result["filtered_count"],
        torrents_scanned=result["torrents_scanned"],
        dry_run=result["dry_run"],
        details=result["details"],
    )


# ---------------------------------------------------------------------------
# PikPak
# ---------------------------------------------------------------------------


@router.get("/pikpak/queue", response_model=PikPakQueueResponse)
def get_pikpak_queue(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: Optional[str] = Query(default=None),
    _user=Depends(_require_auth),
) -> PikPakQueueResponse:
    """Return PikPak transfer queue from PikpakHistory."""
    repo = OperationsRepo()
    try:
        rows, _next_cursor = repo.list_pikpak_history(limit=limit, cursor=cursor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": {"code": "ops.pikpak.invalid_cursor", "message": str(exc)}}) from exc

    items = [
        PikPakQueueItem(
            id=r["Id"],
            torrent_hash=r.get("TorrentHash"),
            torrent_name=r.get("TorrentName"),
            category=r.get("Category"),
            transfer_status=r.get("TransferStatus"),
            error_message=r.get("ErrorMessage"),
            datetime_added_to_qb=r.get("DateTimeAddedToQb"),
        )
        for r in rows
    ]
    return PikPakQueueResponse(items=items, total=len(items))


@router.post("/pikpak/transfer", response_model=PikPakTransferResponse)
def pikpak_transfer(
    body: PikPakTransferRequest,
    _user=Depends(require_role("admin")),
) -> PikPakTransferResponse:
    """Trigger a batch PikPak transfer run."""
    from javdb.integrations.pikpak.bridge import pikpak_bridge

    try:
        pikpak_bridge(days=body.days, dry_run=body.dry_run)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "ops.pikpak.failed", "message": str(exc)}},
        ) from exc

    # pikpak_bridge returns None — gathering per-torrent stats from PikpakHistory
    # rows added during this run is unreliable without a session tag.  Return
    # None for the three count fields; the frontend should render None as
    # "unknown" rather than showing a misleading numeric value.
    return PikPakTransferResponse(
        transferred=None,
        failed=None,
        skipped=None,
        dry_run=body.dry_run,
        details=[{"note": "Transfer dispatched; see server logs for per-torrent results."}],
    )


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


@router.post("/email/test")
def email_test(
    body: EmailTestRequest,
    _user=Depends(require_role("admin")),
) -> dict:
    """Send a test email to the configured recipient.

    ``body.recipient`` is accepted for forward-compatibility but is currently
    ignored — ``send_email`` targets the module-level ``EMAIL_TO`` constant and
    has no per-call recipient override.  If a custom recipient is provided, the
    email still goes to the configured address and the response notes this.
    """
    from javdb.integrations.notify.email import send_email

    subject = "JAVDB AutoSpider — Test Email"
    body_text = (
        "This is a test email sent from the JAVDB AutoSpider API.\n\n"
        "If you received this, your SMTP configuration is working correctly."
    )

    try:
        ok = send_email(subject, body_text)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_ERR_EMAIL_SEND) from exc

    if not ok:
        raise HTTPException(status_code=500, detail=_ERR_EMAIL_SEND)

    response: dict = {"status": "sent", "subject": subject}
    if body.recipient is not None:
        response["note"] = (
            "Custom recipient is not yet supported; email was sent to the "
            "configured EMAIL_TO address instead."
        )
    return response


@router.get("/email/history", response_model=EmailHistoryResponse)
def email_history(
    status: Optional[str] = Query(default=None),
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _user=Depends(_require_auth),
) -> EmailHistoryResponse:
    """List EmailNotificationHistory, newest first."""
    repo = OperationsRepo()
    try:
        rows, next_cursor = repo.list_email_history(
            status=status,
            limit=limit,
            cursor=cursor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": {"code": "ops.email.invalid_cursor", "message": str(exc)}}) from exc

    items = [
        EmailHistoryItem(
            id=r["Id"],
            session_id=r.get("SessionId"),
            recipient=r.get("Recipient"),
            subject=r.get("Subject"),
            status=r.get("Status"),
            error_message=r.get("ErrorMessage"),
            sent_at=r.get("SentAt"),
            resent_at=r.get("ResentAt"),
        )
        for r in rows
    ]
    return EmailHistoryResponse(items=items, next_cursor=next_cursor)


@router.post("/email/{record_id}/resend")
def email_resend(
    record_id: int,
    _user=Depends(require_role("admin")),
) -> dict:
    """Resend a previously failed email notification."""
    from javdb.integrations.notify.email import send_email

    repo = OperationsRepo()
    record = repo.get_email_history_by_id(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail=_ERR_EMAIL_NOT_FOUND)

    subject = record.get("Subject") or "JAVDB AutoSpider — Resent Notification"
    body_text = (
        f"This message is a resend of a previous notification.\n\n"
        f"Original subject: {subject}\n"
        f"Original sent at: {record.get('SentAt', 'unknown')}"
    )

    try:
        ok = send_email(subject, body_text)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_ERR_EMAIL_RESEND) from exc

    if not ok:
        raise HTTPException(status_code=500, detail=_ERR_EMAIL_RESEND)

    repo.mark_email_resent(record_id)
    return {"status": "resent", "id": record_id}
