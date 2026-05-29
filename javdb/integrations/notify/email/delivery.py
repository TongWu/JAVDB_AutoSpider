"""SMTP delivery and dry-run send behaviour for the email notification pipeline.

Owns ``send_email`` (the SMTP send path plus dry-run fingerprinting and email
history recording) and ``convert_log_to_txt`` (the log-to-attachment copy step).

Extracted verbatim from the pre-split ``email.py`` during ADR-015 Phase 6.
"""

import os
import shutil
import smtplib
from email.message import EmailMessage

from javdb.infra.logging import get_logger

# Import masking utilities
from javdb.infra.masking import mask_email, mask_server

from javdb.integrations.notify.email._config import (
    EMAIL_FROM,
    EMAIL_TO,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_SERVER,
    SMTP_USER,
)
from javdb.integrations.notify.email.report_builder import _plain_to_html

logger = get_logger(__name__)


def convert_log_to_txt(log_path):
    """
    Convert log file to txt file for email attachment.
    Returns the path to the txt file.
    """
    if not os.path.exists(log_path):
        logger.info(f"Log file not found, skipping: {log_path}")
        return None

    file_size = os.path.getsize(log_path)
    if file_size == 0:
        logger.warning(f"Log file is empty (0 bytes): {log_path}")

    # Create txt path by replacing extension
    base_name = os.path.basename(log_path)
    name_without_ext = os.path.splitext(base_name)[0]
    txt_filename = f"{name_without_ext}.txt"
    txt_path = os.path.join(os.path.dirname(log_path), txt_filename)

    # Copy content to txt file
    shutil.copy2(log_path, txt_path)
    logger.info(f"Converted {log_path} → {txt_path} ({file_size} bytes)")

    return txt_path


def send_email(subject, body, attachments=None, dry_run=False):
    """Send email with attachments"""
    if dry_run:
        # The body can carry failed-fetch URLs, session ids, and other
        # bits an operator probably does not want in CI logs verbatim.
        # Emit a fingerprint at INFO and keep the full body at DEBUG so
        # local development can still see it when needed.
        import hashlib as _hashlib
        body_str = body if isinstance(body, str) else str(body)
        body_sha = _hashlib.sha256(body_str.encode('utf-8', 'replace')).hexdigest()[:12]
        logger.info("=" * 60)
        logger.info("[DRY RUN] Email would be sent:")
        logger.info(f"Subject: {subject}")
        logger.info(f"From: {mask_email(EMAIL_FROM)}")
        logger.info(f"To: {mask_email(EMAIL_TO)}")
        logger.info(
            "Body: length=%d sha256_12=%s", len(body_str), body_sha,
        )
        logger.debug("Full body:\n%s", body_str)
        if attachments:
            logger.info(f"Attachments: {attachments}")
        logger.info("=" * 60)
        return True

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = EMAIL_FROM
    msg['To'] = EMAIL_TO
    msg.set_content(body)
    msg.add_alternative(_plain_to_html(body), subtype='html')

    if attachments:
        for file_path in attachments:
            if not os.path.exists(file_path):
                logger.warning(f'Attachment not found, skipping: {file_path}')
                continue
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                logger.warning(f'Attachment is empty (0 bytes), skipping: {file_path}')
                continue
            with open(file_path, 'rb') as f:
                file_data = f.read()
                file_name = os.path.basename(file_path)
                maintype = 'application'
                subtype = 'octet-stream'
                msg.add_attachment(file_data, maintype=maintype, subtype=subtype, filename=file_name)
                logger.info(f'Attached: {file_name} ({file_size} bytes)')

    logger.info(f'Connecting to SMTP server {mask_server(SMTP_SERVER)}:{SMTP_PORT}...')

    # Resolve session id and attachment basenames once, before the SMTP block.
    try:
        from javdb.storage.db import get_active_session_id
        _active_session_id = get_active_session_id()
    except Exception:
        _active_session_id = None
    # Record only filenames actually attached to the message — attachment
    # processing above skips missing / empty files.
    _attachment_names = [
        name for part in msg.iter_attachments()
        if (name := part.get_filename())
    ] or None

    def _record_email_history(status: str, error: str = None) -> None:
        """Defensively write an email history row; never raises."""
        try:
            from javdb.storage.repos.operations_repo import OperationsRepo
            OperationsRepo().append_email_history(
                _active_session_id,
                EMAIL_TO,
                subject,
                status,
                error=error,
                attachments=_attachment_names,
            )
        except Exception as _he:
            logger.warning("Failed to record email history: %s", _he)

    try:
        # ``timeout=30`` so a hung / unreachable SMTP server can't pin the
        # GH Actions job until the workflow-level ceiling. ``smtplib.SMTP``
        # otherwise inherits ``socket._GLOBAL_DEFAULT_TIMEOUT`` (``None``),
        # which blocks indefinitely on connect / login / send.
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        logger.info(f'Email sent successfully to {mask_email(EMAIL_TO)}.')
        _record_email_history('sent')
        return True
    except Exception as e:
        logger.error(f'Failed to send email: {e}')
        _record_email_history('failed', error=str(e))
        return False
