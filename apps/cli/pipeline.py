"""Thin pipeline CLI entrypoint."""

from javdb.pipeline.service import (
    PipelineRunService,
    main,
)

__all__ = ["PipelineRunService", "main"]


if __name__ == "__main__":
    raise SystemExit(PipelineRunService().run())
