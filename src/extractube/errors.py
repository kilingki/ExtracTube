from __future__ import annotations

from pathlib import Path


class PipelineError(Exception):
    """A stage-1 failure. kind distinguishes usage, lock, http, and commit errors."""

    def __init__(
        self,
        message: str,
        *,
        kind: str = "pipeline",
        diagnostic: Path | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.diagnostic = diagnostic
