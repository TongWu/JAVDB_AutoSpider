"""Report subject / body formatting for the email notification pipeline.

Owns the construction of the email subject line and plain-text body: the
report header, per-component sections, pending-mode verification table, health
snapshot block, dedup summary, proxy-ban section, drift diagnosis section, and
the subject-line prefixes. Also owns the Ad-Hoc CSV discovery / parsing helpers
used to populate the report header, the report timestamp resolution, and the
plain-text-to-HTML conversion used by the delivery layer.

Extracted verbatim from the pre-split ``email.py`` during ADR-015 Phase 6.
"""

import os
import re
import sys
import json
import subprocess
import html as html_module
from datetime import datetime, timezone

from javdb.infra.logging import get_logger

# Import path helper for dated subdirectories
from javdb.infra.paths import find_latest_report_in_dated_dirs

from javdb.integrations.notify.email._config import _EMAIL_REPORTS_DIR

logger = get_logger(__name__)


def _parse_github_workflow_run_started_at():
    """
    Parse PIPELINE_WORKFLOW_RUN_STARTED_AT (ISO 8601 UTC, set by CI workflows).
    Returns timezone-aware UTC datetime, or None if unset/invalid.
    """
    raw = os.environ.get('PIPELINE_WORKFLOW_RUN_STARTED_AT', '').strip()
    if not raw:
        return None
    s = raw.replace('Z', '+00:00') if raw.endswith('Z') else raw
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        logger.warning('Invalid PIPELINE_WORKFLOW_RUN_STARTED_AT: %r', raw)
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def get_report_display_datetime():
    """
    Wall-clock moment for email subject (YYYYMMDD) and report headers.

    When CI sets PIPELINE_WORKFLOW_RUN_STARTED_AT to the captured workflow start
    (UTC ISO), use that instant so long runs that cross midnight still show the
    trigger day. Otherwise fall back to datetime.now().
    """
    parsed = _parse_github_workflow_run_started_at()
    if parsed is None:
        return datetime.now()
    tz_name = os.environ.get('TZ', '').strip()
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            return parsed.astimezone(ZoneInfo(tz_name))
        except Exception:
            logger.debug('Could not apply TZ=%r for report display', tz_name)
    return parsed.astimezone(timezone.utc)


def extract_adhoc_info_from_csv(csv_path):
    """
    Extract Ad-Hoc mode information from CSV filename.

    Expected format: Javdb_AdHoc_{type}_{name}_{date}.csv
    Examples:
    - Javdb_AdHoc_actors_森日向子_20251224.csv -> (actors, 森日向子)
    - Javdb_AdHoc_makers_MOODYZ_20251224.csv -> (makers, MOODYZ)
    - Javdb_AdHoc_video_codes_MIDA_20251224.csv -> (video_codes, MIDA)

    Returns:
        tuple: (url_type, display_name) or (None, None) if not parseable
    """
    if not csv_path:
        return None, None

    filename = os.path.basename(csv_path)

    # Check if it's an Ad-Hoc file
    if not filename.startswith('Javdb_AdHoc_'):
        return None, None

    # Remove prefix and extension
    # Javdb_AdHoc_actors_森日向子_20251224.csv -> actors_森日向子_20251224
    without_prefix = filename.replace('Javdb_AdHoc_', '').replace('.csv', '')

    # Split and extract parts
    # actors_森日向子_20251224 -> ['actors', '森日向子', '20251224']
    # video_codes_MIDA_20251224 -> ['video', 'codes', 'MIDA', '20251224']
    #   (multi-part types like video_codes get split into multiple parts)
    parts = without_prefix.split('_')

    if len(parts) < 3:
        return None, None

    # Handle url_type which might be multi-part (e.g., video_codes)
    # The date is always the last part (8 digits)
    date_part = parts[-1]
    if not (len(date_part) == 8 and date_part.isdigit()):
        return None, None

    # Known multi-part types
    multi_part_types = ['video_codes']

    url_type = None
    display_name = None

    # Check if it's a multi-part type
    for multi_type in multi_part_types:
        if without_prefix.startswith(multi_type + '_'):
            url_type = multi_type
            # Extract name between type and date
            name_parts = parts[2:-1]  # Skip first two parts (video, codes) and last (date)
            display_name = '_'.join(name_parts) if name_parts else None
            break

    # If not a multi-part type, assume single part type
    if url_type is None:
        url_type = parts[0]
        # Name is everything between type and date
        name_parts = parts[1:-1]
        display_name = '_'.join(name_parts) if name_parts else None

    return url_type, display_name


def format_adhoc_info(url_type, display_name):
    """
    Format Ad-Hoc information for display in email.

    Returns:
        str: Formatted string like "Actor: 森日向子" or "Video Code: MIDA"
    """
    type_labels = {
        'actors': 'Actor',
        'makers': 'Maker',
        'video_codes': 'Video Code',
        'series': 'Series',
        'directors': 'Director',
        'labels': 'Label',
    }

    label = type_labels.get(url_type, url_type.replace('_', ' ').title() if url_type else 'Unknown')
    name = display_name if display_name else 'Unknown'

    return f"{label}: {name}"


def find_latest_adhoc_csv(adhoc_dir):
    """
    Find the most recently created/modified Ad-Hoc CSV file.

    This function uses wildcard patterns (not date-specific) to handle
    cross-midnight scenarios where spider runs before midnight but
    email notification runs after midnight.

    Args:
        adhoc_dir: Base Ad-Hoc directory (e.g., reports/AdHoc)

    Returns:
        str: Full path to the latest CSV file, or None if not found
    """
    # Use wildcard pattern to find the most recent AdHoc CSV file
    # Pattern: Javdb_AdHoc_*.csv (any date)
    adhoc_pattern = 'Javdb_AdHoc_*.csv'

    latest_file = find_latest_report_in_dated_dirs(adhoc_dir, adhoc_pattern)

    if latest_file:
        logger.info(f"Found Ad-Hoc CSV: {latest_file}")
        return latest_file

    # Fallback: try to find any CSV file (legacy pattern)
    legacy_pattern = 'Javdb_*.csv'
    latest_legacy = find_latest_report_in_dated_dirs(adhoc_dir, legacy_pattern)

    if latest_legacy:
        logger.info(f"Found Ad-Hoc CSV (legacy pattern): {latest_legacy}")
        return latest_legacy

    logger.warning(f"No Ad-Hoc CSV files found in {adhoc_dir}")
    return None


def find_latest_daily_csv(daily_dir):
    """
    Find the most recently created/modified Daily CSV file.

    This function uses wildcard patterns (not date-specific) to handle
    cross-midnight scenarios where spider runs before midnight but
    email notification runs after midnight.

    Args:
        daily_dir: Base Daily Report directory (e.g., reports/DailyReport)

    Returns:
        str: Full path to the latest CSV file, or None if not found
    """
    # Use wildcard pattern to find the most recent Daily CSV file
    # Pattern: Javdb_TodayTitle_*.csv (any date)
    daily_pattern = 'Javdb_TodayTitle_*.csv'

    latest_file = find_latest_report_in_dated_dirs(daily_dir, daily_pattern)

    if latest_file:
        logger.info(f"Found Daily CSV: {latest_file}")
        return latest_file

    logger.warning(f"No Daily CSV files found in {daily_dir}")
    return None


def _format_pending_verify_section(records, alerts):
    """Render the "Pending Mode Verification" body block.

    Returns the section as a string, or ``''`` when there are no
    pending-mode records (so the email renders unchanged on audit-only
    runs).
    """
    if not records:
        return ''
    alerted_keys_per_record = {}
    for key, value, limit, severity, rec in alerts:
        sid = rec.get('session_id')
        alerted_keys_per_record.setdefault(sid, []).append((key, severity))

    lines = ['', '───────────────────────────────',
             '🧪 PENDING MODE VERIFICATION',
             '───────────────────────────────', '']
    for rec in records:
        sid = rec.get('session_id')
        flagged = alerted_keys_per_record.get(sid, [])
        flagged_str = ''
        if flagged:
            crit = [k for k, s in flagged if s == 'critical']
            soft = [k for k, s in flagged if s == 'soft']
            tag_parts = []
            if crit:
                tag_parts.append('[CRITICAL] ' + ', '.join(crit))
            if soft:
                tag_parts.append('[ALERT] ' + ', '.join(soft))
            flagged_str = '   ⚠️  ' + ' | '.join(tag_parts)
        lines.append(
            f"Session {sid}  mode={rec.get('write_mode')}  "
            f"status={rec.get('final_status')}  "
            f"source={rec.get('source')}{flagged_str}"
        )
        lines.append(
            f"  staged={rec.get('pending_staged_count')}  "
            f"applied={rec.get('pending_applied_count')}  "
            f"residual={rec.get('pending_residual_count')}"
        )
        lines.append(
            f"  commit_attempts={rec.get('commit_attempts')}  "
            f"commit_duration_ms={rec.get('commit_duration_ms')}  "
            f"hrefs={rec.get('hrefs_processed')}"
        )
        movies = rec.get('movies_upserted', 0)
        torr_up = rec.get('torrents_upserted', 0)
        torr_del = rec.get('torrents_deleted', 0)
        lines.append(
            f"  movies_upserted={movies}  torrents_upserted={torr_up}  "
            f"torrents_deleted={torr_del}"
        )
        if rec.get('shadow_audit_enabled'):
            lines.append(
                f"  derived_recompute_drift="
                f"{rec.get('derived_recompute_drift', 0)}  "
                f"samples={rec.get('derived_drift_samples') or []}"
            )
        wsf = int(rec.get('worker_stage_rollback_failed', 0) or 0)
        cpm = int(rec.get('cleanup_path_mismatch_count', 0) or 0)
        soc = int(rec.get('staged_claim_orphan_count', 0) or 0)
        if wsf or cpm or soc:
            lines.append(
                f"  worker_stage_rollback_failed={wsf}  "
                f"cleanup_path_mismatch_count={cpm}  "
                f"staged_claim_orphan_count={soc}"
            )
        if rec.get('error'):
            lines.append(f"  error={rec.get('error')}")
        lines.append('')
    if not alerts:
        lines.append('All pending-mode metrics within Phase 3 thresholds.')
    else:
        lines.append('See [PENDING-ALERT] / [PENDING-PAUSE] subject.')
    return '\n'.join(lines)


def _format_health_snapshot_section(snapshot_path):
    """Render the Phase 3 24h Health Snapshot, if available."""
    if not snapshot_path or not os.path.exists(snapshot_path):
        return ''
    try:
        import json as _json
        with open(snapshot_path, 'r', encoding='utf-8') as f:
            snap = _json.load(f)
    except Exception as e:
        logger.warning('Failed to read pending_health_24h.json: %s', e)
        return ''
    if not snap:
        return ''
    lines = ['', '───────────────────────────────',
             '📈 HEALTH SNAPSHOT (24h)',
             '───────────────────────────────', '']
    lines.append(
        f"Window: {snap.get('window_start')} → {snap.get('window_end')}"
    )
    lines.append(
        f"Pending sessions:           {snap.get('pending_session_count', 0)}"
    )
    lines.append(
        f"Successful (committed):     "
        f"{snap.get('successful_committed_count', 0)}"
    )
    lines.append(
        f"Failed (rollback_pending):  "
        f"{snap.get('rolled_back_count', 0)}"
    )
    success_rate = snap.get('success_rate_percent')
    if success_rate is not None:
        lines.append(f"Success rate:               {success_rate:.1f}%")
    lines.append(
        f"Avg commit_duration_ms:     "
        f"{snap.get('avg_commit_duration_ms', 0)}"
    )
    lines.append(
        f"p95 per_movie_ms:           "
        f"{snap.get('p95_per_movie_ms', 0)}"
    )
    lines.append(
        f"Σ commit_attempts:          {snap.get('total_commit_attempts', 0)}"
    )
    lines.append(
        f"Σ derived_recompute_drift:  "
        f"{snap.get('total_derived_recompute_drift', 0)}"
    )
    lines.append(
        f"Σ worker_stage_rollback_failed: "
        f"{snap.get('total_worker_stage_rollback_failed', 0)}"
    )
    lines.append(
        f"Σ stale resume successes:   "
        f"{snap.get('stale_resume_successes', 0)}"
    )
    lines.append(
        f"Σ stale resume failures:    "
        f"{snap.get('stale_resume_failures', 0)}"
    )
    return '\n'.join(lines)


def _resolve_default_verify_jsonl(explicit_path):
    """Pick the verify-jsonl path: CLI arg, env var, then default."""
    if explicit_path:
        return explicit_path
    reports_dir = os.environ.get('REPORTS_DIR', _EMAIL_REPORTS_DIR)
    candidate = os.path.join(reports_dir, 'D1', 'd1_drift.jsonl')
    if os.path.exists(candidate):
        return candidate
    return None


def _resolve_default_health_snapshot(explicit_path):
    if explicit_path:
        return explicit_path
    reports_dir = os.environ.get('REPORTS_DIR', _EMAIL_REPORTS_DIR)
    candidate = os.path.join(reports_dir, 'D1', 'pending_health_24h.json')
    if os.path.exists(candidate):
        return candidate
    return None


def _build_drift_diagnosis_section():
    """Run ``drift_diagnose --since 1 --json`` and return a formatted section.

    ADR-009 D6: invoked when ``_build_dual_drift_advisory()`` already
    returned a non-empty advisory.  Runs the diagnose CLI as a
    subprocess (read-only, NEVER with ``--apply``) and renders the
    results as a plain-text section to append to the email body.

    Returns ``(section_text, suspects_list)`` where *section_text* is
    empty when the subprocess produces no suspects, and *suspects_list*
    is the raw list for use by ``_drift_diagnosis_subject_prefix``.
    Returns a fallback message on any subprocess failure so that email
    delivery is never blocked; in that case *suspects_list* is empty.
    """
    _MANUAL_CMD = 'python3 -m apps.cli.db.drift_diagnose --since 1 --json'

    try:
        result = subprocess.run(
            [sys.executable, '-m', 'apps.cli.db.drift_diagnose',
             '--since', '1', '--json'],
            capture_output=True, text=True, timeout=60,
        )
        stdout = result.stdout.strip()
    except subprocess.TimeoutExpired:
        return (
            '─── Drift Diagnosis ───\n'
            'Automated diagnosis unavailable: subprocess timed out after 60s.\n'
            f'Run manually: {_MANUAL_CMD}\n\n'
        ), []
    except Exception as exc:
        return (
            '─── Drift Diagnosis ───\n'
            f'Automated diagnosis unavailable: {exc}\n'
            f'Run manually: {_MANUAL_CMD}\n\n'
        ), []

    if result.returncode not in (0, 1, 2):
        return (
            '─── Drift Diagnosis ───\n'
            f'Automated diagnosis unavailable: unexpected exit code {result.returncode}.\n'
            f'Run manually: {_MANUAL_CMD}\n\n'
        ), []

    try:
        data = json.loads(stdout)
    except (ValueError, TypeError):
        return (
            '─── Drift Diagnosis ───\n'
            'Automated diagnosis unavailable: non-JSON output from subprocess.\n'
            f'Run manually: {_MANUAL_CMD}\n\n'
        ), []
    if not isinstance(data, dict):
        return (
            '─── Drift Diagnosis ───\n'
            'Automated diagnosis unavailable: invalid JSON schema from subprocess.\n'
            f'Run manually: {_MANUAL_CMD}\n\n'
        ), []

    suspects = data.get('suspects', [])
    if not isinstance(suspects, list):
        return (
            '─── Drift Diagnosis ───\n'
            'Automated diagnosis unavailable: invalid suspects payload.\n'
            f'Run manually: {_MANUAL_CMD}\n\n'
        ), []
    if not suspects:
        return '', []

    lines = ['─── Drift Diagnosis ───']
    for s in suspects:
        sid = s.get('session_id', '?')
        verdict = s.get('verdict', '?')
        orphan_m = s.get('d1_orphan_movie_count', 0)
        orphan_t = s.get('d1_orphan_torrent_count', 0)
        lines.append(f'  Session: {sid}')
        lines.append(f'    verdict: {verdict}')
        lines.append(f'    orphan movies: {orphan_m}, orphan torrents: {orphan_t}')
        if s.get('suggested_command'):
            lines.append(f'    suggested fix: {s["suggested_command"]}')
        if s.get('note'):
            lines.append(f'    note: {s["note"]}')
    lines.append('')
    return '\n'.join(lines) + '\n', suspects


def _build_ops_diagnosis_advisory(path: str | None) -> str:
    """Return a short ADR-026 advisory from a diagnosis JSON payload."""
    if not path:
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""

    incident_id = payload.get("incident_id", "unknown")
    incident_type = payload.get("incident_type", "unknown")
    confidence = payload.get("confidence", "low")
    persistence_status = payload.get("persistence_status", "")
    findings = payload.get("confirmed_findings") or []
    actions = payload.get("recommended_next_actions") or []
    first_finding = findings[0] if findings else "No confirmed finding recorded."
    first_action = actions[0] if actions else "Review the persisted diagnosis record."
    full_diagnosis = (
        f"/api/diag/ops-incidents/{incident_id}"
        if persistence_status == "d1_written"
        else "Stored in the workflow artifact JSONL fallback; the API record may be unavailable."
    )

    return f"""
─── Operations Diagnosis ───
Incident: {incident_id}
Type: {incident_type}
Confidence: {confidence}
Finding: {first_finding}
Next action: {first_action}
Full diagnosis: {full_diagnosis}

"""


def _drift_diagnosis_subject_prefix(suspects):
    """Return a subject-line prefix tag based on drift diagnosis verdicts.

    * ``[DRIFT-ESCALATE] `` when any suspect is ``ESCALATE_LIVE_DIVERGENCE``
      or ``UNEXPECTED_PATTERN`` (takes priority).
    * ``[DRIFT-FIX-READY] `` when at least one is ``SAFE_TO_APPLY`` and
      none escalate.
    * ``''`` when all suspects are ``CLEAN`` or the list is empty.
    """
    if not suspects:
        return ''

    has_escalate = False
    has_safe = False
    for s in suspects:
        verdict = s.get('verdict', '')
        if verdict in ('ESCALATE_LIVE_DIVERGENCE', 'UNEXPECTED_PATTERN'):
            has_escalate = True
        elif verdict == 'SAFE_TO_APPLY':
            has_safe = True

    if has_escalate:
        return '[DRIFT-ESCALATE] '
    if has_safe:
        return '[DRIFT-FIX-READY] '
    return ''


def _build_pending_subject_prefix(records, alerts, has_critical, mode):
    """Return the subject-line prefix for pending-mode results.

    Returns ``''`` when there are no pending records or no alerts.

    * Critical → ``[PENDING-PAUSE]`` (ADR-006 PR-D: the email job's
      ``Alert + pause on critical pending alert`` step writes
      ``pipeline_paused_until`` into ``.publish-config.yml`` so the
      next run is skipped by the pause gate). Pre-ADR-006 this prefix
      was ``[PENDING-ROLLBACK-AUTO]``, when the same trigger flipped
      the next run to audit mode for 24h instead of pausing.
    * Soft     → ``[PENDING-ALERT]``.

    The first alert's summary is appended in parentheses so the operator
    sees the trigger without opening the email body.
    """
    if not records:
        return ''
    if not alerts:
        return ''
    first = alerts[0]
    field, value, limit, severity, rec = first
    sid = rec.get('session_id')
    summary = f"{field}={value:g} > {limit:g} session={sid}"
    if has_critical:
        return f"[PENDING-PAUSE] ({summary}) "
    return f"[PENDING-ALERT] ({summary}) "


def format_email_report(spider_stats, uploader_stats, pikpak_stats, ban_summary,
                        show_spider=True, show_uploader=True, show_pikpak=True,
                        mode='daily', adhoc_info=None, proxy_ban_html_summary=None,
                        dedup_stats=None, report_dt=None, report_end_dt=None,
                        pending_verify_records=None, pending_alerts=None,
                        health_snapshot_path=None):
    """
    Format a mobile-friendly email report.
    Only includes sections for components that ran successfully.

    Args:
        mode: 'daily' or 'adhoc'
        adhoc_info: Formatted Ad-Hoc info string (e.g., "Actor: 森日向子")
        proxy_ban_html_summary: Summary of proxy ban HTML files captured (if any)
        report_dt: Pipeline / workflow start time for header (default: now)
        report_end_dt: Email compose / send-side end time (default: now at format time)
    """
    sections = []
    start_dt = report_dt if report_dt is not None else datetime.now()
    if report_end_dt is not None:
        end_dt = report_end_dt
    elif start_dt.tzinfo is not None:
        end_dt = datetime.now(tz=start_dt.tzinfo)
    else:
        end_dt = datetime.now()
    start_str = start_dt.strftime('%Y-%m-%d %H:%M:%S')
    end_str = end_dt.strftime('%Y-%m-%d %H:%M:%S')

    # Determine mode display
    if mode == 'adhoc':
        mode_display = "Ad-Hoc"
        if adhoc_info:
            mode_detail = f"Mode: {mode_display}\nTarget: {adhoc_info}"
        else:
            mode_detail = f"Mode: {mode_display}"
    else:
        mode_display = "Daily"
        mode_detail = f"Mode: {mode_display}"

    # Header
    sections.append(f"""
═══════════════════════════════
JavDB Pipeline Report ({mode_display})
Started:  {start_str}
Finished: {end_str}
═══════════════════════════════

{mode_detail}""")

    # Spider section
    # Note: Statistics are for MOVIES (unique pages), not individual torrent links
    if show_spider:
        # Calculate totals for verification (include failed count and no_new_torrents)
        p1_total = spider_stats['phase1']['processed'] + spider_stats['phase1']['skipped_history'] + spider_stats['phase1']['failed'] + spider_stats['phase1'].get('no_new_torrents', 0)
        p2_total = spider_stats['phase2']['processed'] + spider_stats['phase2']['skipped_history'] + spider_stats['phase2']['failed'] + spider_stats['phase2'].get('no_new_torrents', 0)
        overall_total = spider_stats['overall']['successfully_processed'] + spider_stats['overall']['skipped_history'] + spider_stats['overall']['failed'] + spider_stats['overall'].get('no_new_torrents', 0)

        # Use None check instead of `or` to handle 0 correctly
        p1_discovered = spider_stats['phase1']['discovered'] if spider_stats['phase1']['discovered'] is not None else p1_total
        p2_discovered = spider_stats['phase2']['discovered'] if spider_stats['phase2']['discovered'] is not None else p2_total
        overall_discovered = spider_stats['overall']['total_discovered'] if spider_stats['overall']['total_discovered'] is not None else overall_total

        p1_proc = spider_stats['phase1']['processed']
        p2_proc = spider_stats['phase2']['processed']
        p1_skip = spider_stats['phase1']['skipped_history']
        p2_skip = spider_stats['phase2']['skipped_history']
        p1_nonew = spider_stats['phase1'].get('no_new_torrents', 0)
        p2_nonew = spider_stats['phase2'].get('no_new_torrents', 0)
        p1_fail = spider_stats['phase1']['failed']
        p2_fail = spider_stats['phase2']['failed']

        overall_proc = spider_stats['overall']['successfully_processed']
        overall_skip = spider_stats['overall']['skipped_history']
        overall_nonew = spider_stats['overall'].get('no_new_torrents', 0)
        overall_fail = spider_stats['overall']['failed']

        spider_block = f"""
📊 SPIDER STATISTICS (Movies)
───────────────────────────────

  Discovered:        {overall_discovered} (P1: {p1_discovered}, P2: {p2_discovered})
  Processed:         {overall_proc} (P1: {p1_proc}, P2: {p2_proc})
  Skipped (History): {overall_skip} (P1: {p1_skip}, P2: {p2_skip})
  No New Torrents:   {overall_nonew} (P1: {p1_nonew}, P2: {p2_nonew})
  Failed:            {overall_fail} (P1: {p1_fail}, P2: {p2_fail})"""

        failed_movies = spider_stats.get('failed_movies') or []
        if failed_movies:
            lines = ["\n\n  Failed Movies:"]
            for fm in failed_movies:
                vc = fm.get('video_code', '?')
                url = fm.get('url', '')
                lines.append(f"    • {vc}  {url}")
            spider_block += "\n".join(lines)

        sections.append(spider_block)

    # Uploader section
    if show_uploader:
        sections.append(f"""
───────────────────────────────
📤 QBITTORRENT UPLOADER
───────────────────────────────

Upload Summary
  Total: {uploader_stats['total']}
  Success: {uploader_stats['success']} ({uploader_stats['success_rate']:.1f}%)
  Failed: {uploader_stats['failed']}

Breakdown by Type
  Hacked (Sub): {uploader_stats['hacked_sub']}
  Hacked (NoSub): {uploader_stats['hacked_nosub']}
  Regular (Sub): {uploader_stats['subtitle']}
  Regular (NoSub): {uploader_stats['no_subtitle']}""")

    # PikPak section
    if show_pikpak:
        sections.append(f"""
───────────────────────────────
🔄 PIKPAK BRIDGE
───────────────────────────────

Cleanup (>{pikpak_stats['threshold_days']} days)
  Scanned: {pikpak_stats['total_torrents']}
  Filtered: {pikpak_stats['filtered_old']}
  Added to PikPak: {pikpak_stats['added_to_pikpak']}
  Removed from QB: {pikpak_stats['removed_from_qb']}
  Failed: {pikpak_stats['failed']}""")

    # Dedup section
    if dedup_stats:
        deleted_list = "\n".join(dedup_stats['deleted_items'][:10]) if dedup_stats['deleted_items'] else "  (none this run)"
        if len(dedup_stats['deleted_items']) > 10:
            deleted_list += f"\n  ... and {len(dedup_stats['deleted_items']) - 10} more"
        redownload_detected = dedup_stats.get('redownload_detected', 0)
        redownload_deleted = dedup_stats.get('redownload_deleted', 0)
        regular_detected = max(dedup_stats['detected'] - redownload_detected, 0)
        regular_deleted = max(dedup_stats['deleted'] - redownload_deleted, 0)
        breakdown_block = ""
        if redownload_detected or redownload_deleted:
            breakdown_block = f"""

Breakdown
  Regular Upgrades:   {regular_detected} detected, {regular_deleted} deleted
  Redownload Upgrade: {redownload_detected} detected, {redownload_deleted} deleted"""
        sections.append(f"""
───────────────────────────────
🗑️ RCLONE DEDUP SUMMARY
───────────────────────────────

Detected for Dedup: {dedup_stats['detected']}
Successfully Deleted: {dedup_stats['deleted']}
Failed: {dedup_stats['failed']}{breakdown_block}

Deleted This Run:
{deleted_list}""")

    # Proxy ban HTML files section (only show if there are captured files)
    if proxy_ban_html_summary:
        sections.append(f"""
───────────────────────────────
📄 PROXY BAN DEBUG FILES
───────────────────────────────

{proxy_ban_html_summary}

(See attached files for full HTML content — zipped if more than 3)""")

    # Proxy status (always show)
    sections.append(f"""
───────────────────────────────
🚦 PROXY STATUS
───────────────────────────────

{ban_summary}""")

    # Pending Mode Verification + Health Snapshot (Phase 2 / 3).
    # The 24h health snapshot is rendered independently so audit-only
    # and zero-pending runs still surface it.
    if pending_verify_records:
        verify_block = _format_pending_verify_section(
            pending_verify_records, pending_alerts or [],
        )
        if verify_block:
            sections.append(verify_block)
    snapshot_block = _format_health_snapshot_section(
        health_snapshot_path,
    )
    if snapshot_block:
        sections.append(snapshot_block)

    sections.append("""
═══════════════════════════════
End of Report
═══════════════════════════════""")

    return "\n".join(sections)


def _plain_to_html(text):
    """Convert plain-text report body to HTML with clickable URLs."""
    escaped = html_module.escape(text)
    linked = re.sub(
        r'(https?://\S+)',
        r'<a href="\1">\1</a>',
        escaped,
    )
    return (
        '<html><body>'
        '<pre style="font-family:monospace;white-space:pre-wrap;">'
        f'{linked}'
        '</pre></body></html>'
    )
