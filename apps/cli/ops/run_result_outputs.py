from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Mapping


def outputs_from_result(path: str | Path) -> dict[str, str]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    stats = raw.get("stats") or {}
    outputs = {
        "csv_filename": raw.get("csv_path") or "",
        "session_id": raw.get("session_id") or "",
        "dedup_csv_path": raw.get("dedup_csv_path") or "",
        "stat_pages": str(stats.get("pages") if stats.get("pages") is not None else ""),
        "stat_found": str(stats.get("found") if stats.get("found") is not None else ""),
        "stat_parsed": str(stats.get("parsed") if stats.get("parsed") is not None else ""),
        "stat_skipped": str(stats.get("skipped") if stats.get("skipped") is not None else ""),
        "stat_failed": str(stats.get("failed") if stats.get("failed") is not None else ""),
        "stat_no_new": str(stats.get("no_new") if stats.get("no_new") is not None else ""),
    }
    return {key: value for key, value in outputs.items() if value != ""}


def write_github_output(path: str | Path, outputs: Mapping[str, str]) -> None:
    with open(path, "a", encoding="utf-8") as fp:
        for key, value in outputs.items():
            fp.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write GitHub outputs from a run result JSON file.")
    parser.add_argument("--result-json", required=True)
    parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT"))
    args = parser.parse_args(argv)
    outputs = outputs_from_result(args.result_json)
    if args.github_output:
        write_github_output(args.github_output, outputs)
    for key, value in outputs.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
