"""Thin pipeline CLI entrypoint."""

from packages.python.javdb_platform.pipeline_service import (
    PipelineRunService,
    main,
)

__all__ = ["PipelineRunService", "main"]


if __name__ == "__main__":
    raise SystemExit(PipelineRunService().run())
