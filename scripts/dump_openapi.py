"""Dump the FastAPI app's OpenAPI schema to docs/api/openapi.json."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Use the production app exactly as it runs.
from apps.api.services.runtime import app  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "docs" / "api" / "openapi.json"


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    schema = app.openapi()
    OUT.write_text(json.dumps(schema, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
