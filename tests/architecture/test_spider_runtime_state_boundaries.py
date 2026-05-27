from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]

PRODUCTION_GLOBS = [
    "javdb/spider/**/*.py",
]

ALLOWED_FILES = {
    "javdb/spider/runtime/state.py",
    "javdb/spider/runtime/context.py",
}

FORBIDDEN_DIRECT_STATE_FIELDS = {
    "parsed_links",
    "proxy_ban_html_files",
    "global_proxy_pool",
    "global_request_handler",
    "global_proxy_coordinator",
    "global_login_state_client",
    "global_movie_claim_client",
    "global_runner_registry_client",
    "global_recommend_proxy_policy",
    "global_work_distributor_client",
    "runtime_holder_id",
    "login_attempted",
    "refreshed_session_cookie",
    "logged_in_proxy_name",
    "current_login_state_version",
    "login_attempts_per_proxy",
    "login_failures_per_proxy",
    "login_total_attempts",
    "login_total_budget",
    "always_bypass_time",
    "proxies_requiring_cf_bypass",
}


def _production_files():
    for pattern in PRODUCTION_GLOBS:
        for path in ROOT.glob(pattern):
            rel = path.relative_to(ROOT).as_posix()
            if rel in ALLOWED_FILES:
                continue
            if "__pycache__" in rel:
                continue
            yield path


def test_production_code_does_not_directly_use_legacy_state_fields():
    field_group = "|".join(
        re.escape(name) for name in sorted(FORBIDDEN_DIRECT_STATE_FIELDS)
    )
    direct_pattern = re.compile(
        r"\b(?:state|_state)\.("
        + field_group
        + r")\b"
    )
    getattr_pattern = re.compile(
        r"\bgetattr\(\s*(?:state|_state)\s*,\s*['\"]("
        + field_group
        + r")['\"]"
    )
    alias_pattern = re.compile(
        r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=.*\b(?:else\s+|=\s*)(?:state|_state)\b"
    )
    indirect_template = (
        r"\b(?:{aliases})\.("
        + "|".join(re.escape(name) for name in sorted(FORBIDDEN_DIRECT_STATE_FIELDS))
        + r")\b"
    )
    offenders: list[str] = []
    for path in _production_files():
        text = path.read_text(encoding="utf-8")
        state_aliases = {
            match.group(1)
            for match in alias_pattern.finditer(text)
            if match.group(1) not in {"state", "_state"}
        }
        indirect_pattern = (
            re.compile(
                indirect_template.format(
                    aliases="|".join(re.escape(alias) for alias in sorted(state_aliases))
                )
            )
            if state_aliases
            else None
        )
        for line_no, line in enumerate(text.splitlines(), start=1):
            if (
                direct_pattern.search(line)
                or getattr_pattern.search(line)
                or (indirect_pattern is not None and indirect_pattern.search(line))
            ):
                offenders.append(f"{path.relative_to(ROOT)}:{line_no}: {line.strip()}")

    assert offenders == []
