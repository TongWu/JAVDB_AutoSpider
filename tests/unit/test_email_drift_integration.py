"""Tests for drift diagnosis integration in the email notification job.

ADR-009 D6: when ``_build_dual_drift_advisory()`` returns a non-empty
advisory, the email job additionally invokes ``drift_diagnose --since 1
--json`` as a subprocess and appends a diagnosis section to the email
body.  The subject line receives a tag (``[DRIFT-FIX-READY]`` or
``[DRIFT-ESCALATE]``) depending on the verdict.

Tests exercise the two new helpers in isolation:
* ``_build_drift_diagnosis_section()``
* ``_drift_diagnosis_subject_prefix()``
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

# Ensure the project root is importable.
project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from javdb.integrations.notify.email import (
    _build_drift_diagnosis_section,
    _drift_diagnosis_subject_prefix,
)


# ── Fixtures ────────────────────────────────────────────────────────────


def _make_diagnose_json(suspects, max_verdict="CLEAN"):
    """Build a JSON string matching ``drift_diagnose --json`` output."""
    return json.dumps({"suspects": suspects, "max_verdict": max_verdict})


def _safe_suspect(
    session_id="20260523T010000.000000Z-0001-0001",
    orphan_movies=3,
    orphan_torrents=5,
):
    return {
        "session_id": session_id,
        "provenance": "verify_metric",
        "d1_orphan_movie_count": orphan_movies,
        "d1_orphan_torrent_count": orphan_torrents,
        "verdict": "SAFE_TO_APPLY",
        "suggested_command": (
            f"python3 -m apps.cli.db.drift_diagnose --apply "
            f"--session-id {session_id}"
        ),
    }


def _escalate_suspect(session_id="20260523T020000.000000Z-0002-0001"):
    return {
        "session_id": session_id,
        "provenance": "d1_sweep",
        "d1_orphan_movie_count": 2,
        "d1_orphan_torrent_count": 0,
        "verdict": "ESCALATE_LIVE_DIVERGENCE",
    }


def _unexpected_suspect(session_id="20260523T030000.000000Z-0003-0001"):
    return {
        "session_id": session_id,
        "provenance": "verify_metric",
        "d1_orphan_movie_count": 1,
        "d1_orphan_torrent_count": 0,
        "verdict": "UNEXPECTED_PATTERN",
        "note": "Session status is 'in_progress', expected 'committed'",
    }


def _clean_suspect(session_id="20260523T040000.000000Z-0004-0001"):
    return {
        "session_id": session_id,
        "provenance": "verify_metric",
        "d1_orphan_movie_count": 0,
        "d1_orphan_torrent_count": 0,
        "verdict": "CLEAN",
    }


# ═══════════════════════════════════════════════════════════════════════
#  _build_drift_diagnosis_section
# ═══════════════════════════════════════════════════════════════════════


class TestBuildDriftDiagnosisSection:
    """Tests for ``_build_drift_diagnosis_section()``.

    The function returns ``(section_text, suspects_list)``.
    """

    @patch("javdb.integrations.notify.email.subprocess.run")
    def test_safe_to_apply_renders_section_with_suggested_command(self, mock_run):
        suspect = _safe_suspect()
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout=_make_diagnose_json([suspect], "SAFE_TO_APPLY"),
            stderr="",
        )

        section, suspects = _build_drift_diagnosis_section()

        assert "Drift Diagnosis" in section
        assert suspect["session_id"] in section
        assert "SAFE_TO_APPLY" in section
        assert "orphan movies: 3" in section
        assert "orphan torrents: 5" in section
        assert suspect["suggested_command"] in section
        assert len(suspects) == 1
        assert suspects[0]["verdict"] == "SAFE_TO_APPLY"
        # Must NEVER contain --apply
        call_args = mock_run.call_args[0][0]
        assert "--apply" not in call_args

    @patch("javdb.integrations.notify.email.subprocess.run")
    def test_escalate_renders_section(self, mock_run):
        suspect = _escalate_suspect()
        mock_run.return_value = MagicMock(
            returncode=2,
            stdout=_make_diagnose_json([suspect], "ESCALATE_LIVE_DIVERGENCE"),
            stderr="",
        )

        section, suspects = _build_drift_diagnosis_section()

        assert "Drift Diagnosis" in section
        assert suspect["session_id"] in section
        assert "ESCALATE_LIVE_DIVERGENCE" in section
        assert len(suspects) == 1

    @patch("javdb.integrations.notify.email.subprocess.run")
    def test_no_suspects_returns_empty(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=_make_diagnose_json([], "CLEAN"),
            stderr="",
        )

        section, suspects = _build_drift_diagnosis_section()

        assert section == ""
        assert suspects == []

    @patch("javdb.integrations.notify.email.subprocess.run")
    def test_subprocess_timeout_returns_fallback(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd="drift_diagnose", timeout=60,
        )

        section, suspects = _build_drift_diagnosis_section()

        assert "Automated diagnosis unavailable" in section
        assert "timed out" in section.lower()
        assert "apps.cli.db.drift_diagnose" in section
        assert suspects == []

    @patch("javdb.integrations.notify.email.subprocess.run")
    def test_subprocess_non_json_output_returns_fallback(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="This is not JSON\n",
            stderr="",
        )

        section, suspects = _build_drift_diagnosis_section()

        assert "Automated diagnosis unavailable" in section
        assert "apps.cli.db.drift_diagnose" in section
        assert suspects == []

    @patch("javdb.integrations.notify.email.subprocess.run")
    def test_subprocess_crash_returns_fallback(self, mock_run):
        mock_run.side_effect = OSError("No such file or directory")

        section, suspects = _build_drift_diagnosis_section()

        assert "Automated diagnosis unavailable" in section
        assert "apps.cli.db.drift_diagnose" in section
        assert suspects == []

    @patch("javdb.integrations.notify.email.subprocess.run")
    def test_exit_code_0_1_2_all_handled(self, mock_run):
        """Exit codes 0/1/2 are all valid diagnose-mode exits."""
        for exit_code, verdict in [(0, "CLEAN"), (1, "SAFE_TO_APPLY"), (2, "ESCALATE_LIVE_DIVERGENCE")]:
            suspect = (
                _clean_suspect() if exit_code == 0
                else _safe_suspect() if exit_code == 1
                else _escalate_suspect()
            )
            mock_run.return_value = MagicMock(
                returncode=exit_code,
                stdout=_make_diagnose_json(
                    [suspect] if exit_code != 0 else [],
                    verdict,
                ),
                stderr="",
            )
            section, _suspects = _build_drift_diagnosis_section()
            # Should not produce a fallback message for valid exit codes
            assert "unavailable" not in section

    @patch("javdb.integrations.notify.email.subprocess.run")
    def test_multiple_suspects_all_rendered(self, mock_run):
        suspects = [_safe_suspect(), _escalate_suspect()]
        mock_run.return_value = MagicMock(
            returncode=2,
            stdout=_make_diagnose_json(suspects, "ESCALATE_LIVE_DIVERGENCE"),
            stderr="",
        )

        section, returned_suspects = _build_drift_diagnosis_section()

        for s in suspects:
            assert s["session_id"] in section
        assert len(returned_suspects) == 2

    @patch("javdb.integrations.notify.email.subprocess.run")
    def test_subprocess_uses_sys_executable(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=_make_diagnose_json([], "CLEAN"),
            stderr="",
        )

        _build_drift_diagnosis_section()

        call_args = mock_run.call_args[0][0]
        assert call_args[0] == sys.executable

    @patch("javdb.integrations.notify.email.subprocess.run")
    def test_subprocess_has_timeout_60(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=_make_diagnose_json([], "CLEAN"),
            stderr="",
        )

        _build_drift_diagnosis_section()

        assert mock_run.call_args[1]["timeout"] == 60

    @patch("javdb.integrations.notify.email.subprocess.run")
    def test_note_field_rendered_when_present(self, mock_run):
        suspect = _unexpected_suspect()
        mock_run.return_value = MagicMock(
            returncode=2,
            stdout=_make_diagnose_json([suspect], "UNEXPECTED_PATTERN"),
            stderr="",
        )

        section, _suspects = _build_drift_diagnosis_section()

        assert suspect["note"] in section


# ═══════════════════════════════════════════════════════════════════════
#  _drift_diagnosis_subject_prefix
# ═══════════════════════════════════════════════════════════════════════


class TestDriftDiagnosisSubjectPrefix:
    """Tests for ``_drift_diagnosis_subject_prefix()``."""

    def test_safe_to_apply_returns_fix_ready(self):
        suspects = [_safe_suspect()]
        result = _drift_diagnosis_subject_prefix(suspects)
        assert result == "[DRIFT-FIX-READY] "

    def test_escalate_returns_escalate(self):
        suspects = [_escalate_suspect()]
        result = _drift_diagnosis_subject_prefix(suspects)
        assert result == "[DRIFT-ESCALATE] "

    def test_unexpected_returns_escalate(self):
        suspects = [_unexpected_suspect()]
        result = _drift_diagnosis_subject_prefix(suspects)
        assert result == "[DRIFT-ESCALATE] "

    def test_all_clean_returns_empty(self):
        suspects = [_clean_suspect()]
        result = _drift_diagnosis_subject_prefix(suspects)
        assert result == ""

    def test_no_suspects_returns_empty(self):
        result = _drift_diagnosis_subject_prefix([])
        assert result == ""

    def test_safe_and_escalate_escalate_takes_priority(self):
        """When both SAFE_TO_APPLY and ESCALATE are present, ESCALATE wins."""
        suspects = [_safe_suspect(), _escalate_suspect()]
        result = _drift_diagnosis_subject_prefix(suspects)
        assert result == "[DRIFT-ESCALATE] "

    def test_safe_and_unexpected_escalate_takes_priority(self):
        suspects = [_safe_suspect(), _unexpected_suspect()]
        result = _drift_diagnosis_subject_prefix(suspects)
        assert result == "[DRIFT-ESCALATE] "

    def test_clean_and_safe_fix_ready(self):
        suspects = [_clean_suspect(), _safe_suspect()]
        result = _drift_diagnosis_subject_prefix(suspects)
        assert result == "[DRIFT-FIX-READY] "
