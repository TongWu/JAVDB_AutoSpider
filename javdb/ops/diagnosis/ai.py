"""AI synthesis boundary for ADR-026 operations diagnosis."""

from __future__ import annotations

from collections.abc import Callable

from javdb.infra.config import cfg
from javdb.ops.diagnosis.models import DiagnosisResult, IncidentBundle


Synthesizer = Callable[[IncidentBundle, DiagnosisResult], DiagnosisResult]


def _cfg_bool(name: str, default: bool = False) -> bool:
    raw = cfg(name, default)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return bool(default)


def ai_diagnosis_enabled() -> bool:
    return _cfg_bool("OPS_DIAGNOSIS_AI_ENABLED", False)


def synthesize_with_configured_ai(
    bundle: IncidentBundle,
    detector_result: DiagnosisResult,
) -> DiagnosisResult:
    """Return detector result until a configured model adapter is implemented.

    Phase 1 keeps the interface explicit while allowing deployments without
    AI credentials to remain fully useful. Online model calls are intentionally
    left outside this phase; callers already depend on this stable synthesis
    boundary.
    """
    if not ai_diagnosis_enabled():
        return detector_result
    return detector_result
