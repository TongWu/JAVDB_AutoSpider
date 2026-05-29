"""Canonical rclone manager CLI entrypoint.

Owns argparse parsing and exit-code mapping for the rclone manager. The
orchestration lives in :mod:`javdb.integrations.rclone.manager.service`.
"""

from __future__ import annotations

from pathlib import Path
import argparse
import sys

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from javdb.integrations.rclone.manager.options import RcloneManagerOptions
from javdb.integrations.rclone.manager.service import run_manager


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Unified rclone manager — scan, report & execute via composable flags',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --scan
  %(prog)s --scan --root-path "gdrive:/path" --years "2025,2026"
  %(prog)s --report
  %(prog)s --scan --report
  %(prog)s --execute
  %(prog)s --report --execute --dry-run
  %(prog)s --scan --report --execute
        """,
    )

    mode_group = parser.add_argument_group('mode flags (at least one required)')
    mode_group.add_argument('--scan', action='store_true', help='Scan remote folder tree into DB/CSV')
    mode_group.add_argument('--report', action='store_true', help='Generate dedup report from inventory')
    mode_group.add_argument('--execute', action='store_true', help='Execute pending deletions from dedup CSV')
    mode_group.add_argument('--execute-soft-delete', action='store_true', help='Execute soft-delete moves from CSV plan')
    mode_group.add_argument(
        '--validate', action='store_true',
        help='Re-validate inventory against the remote (dirs-only listing); '
             'prunes orphan inventory rows and self-heals related dedup pendings',
    )

    parser.add_argument('--root-path', type=str, default=None, help='rclone path (remote:/path)')
    parser.add_argument('--years', type=str, default=None, help='Comma-separated years')
    parser.add_argument('--workers', type=int, default=4, help='Parallel workers (default: 4)')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], default='INFO')
    parser.add_argument('--output', type=str, default=None, help='Override output CSV path')

    report_group = parser.add_argument_group('report options')
    report_group.add_argument('--incremental', action='store_true', help='Only process recent changes')

    execute_group = parser.add_argument_group('execute options')
    execute_group.add_argument('--dry-run', action='store_true', help='Simulate without deleting')
    execute_group.add_argument(
        '--dedup-csv', type=str, default=None,
        help='Override dedup CSV path (default: REPORTS_DIR/DEDUP_CSV)',
    )
    execute_group.add_argument(
        '--soft-delete-csv', type=str, default=None,
        help='Soft-delete CSV path (default: REPORTS_DIR/SOFT_DELETE_CSV)',
    )
    execute_group.add_argument(
        '--soft-delete-backup-prefix', type=str, default='',
        help='Backup destination prefix for rows without destination_path',
    )

    validate_group = parser.add_argument_group('validate options')
    validate_group.add_argument(
        '--no-validate-prune', dest='validate_prune', action='store_false',
        default=True,
        help='Validate mode: only report orphans, do not delete from inventory',
    )

    args = parser.parse_args(argv)

    if not (args.scan or args.report or args.execute or args.execute_soft_delete or args.validate):
        parser.error("At least one mode flag is required")
    if args.scan and args.execute and not args.report:
        parser.error("--scan --execute requires --report (use --scan --report --execute)")
    if args.validate and (args.scan or args.report or args.execute or args.execute_soft_delete):
        parser.error("--validate must be used on its own (no other mode flag)")

    return args


def _parse_years(raw_years: str | None) -> list[str] | None:
    if not raw_years:
        return None
    years = [year.strip() for year in raw_years.split(",") if year.strip()]
    return years or None


def options_from_args(args: argparse.Namespace) -> RcloneManagerOptions:
    return RcloneManagerOptions(
        scan=args.scan,
        report=args.report,
        execute=args.execute,
        execute_soft_delete=args.execute_soft_delete,
        validate=args.validate,
        root_path=args.root_path,
        years=_parse_years(args.years),
        workers=args.workers,
        log_level=args.log_level,
        output=args.output,
        incremental=args.incremental,
        dry_run=args.dry_run,
        dedup_csv=args.dedup_csv,
        soft_delete_csv=args.soft_delete_csv,
        soft_delete_backup_prefix=args.soft_delete_backup_prefix,
        validate_prune=args.validate_prune,
    )


def main(argv: list[str] | None = None) -> int:
    return run_manager(options_from_args(parse_args(argv))).exit_code


if __name__ == "__main__":
    raise SystemExit(main())
