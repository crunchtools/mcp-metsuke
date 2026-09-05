"""Error hierarchy for mcp-metsuke-crunchtools."""

from __future__ import annotations


class MetsukeError(Exception):
    """Base error for all metsuke operations."""


class DefinitionNotFoundError(MetsukeError):
    """Raised when a report definition does not exist."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Report definition not found: {name}")


class OutputNotFoundError(MetsukeError):
    """Raised when no gathered output exists for a report."""

    def __init__(self, name: str, on_date: str | None = None) -> None:
        if on_date:
            super().__init__(f"No output for report '{name}' on date {on_date}")
        else:
            super().__init__(f"No output found for report: {name}")


class OutputIdNotFoundError(MetsukeError):
    """Raised when no gathered output exists for a given output id."""

    def __init__(self, output_id: int) -> None:
        super().__init__(f"No output found with id: {output_id}")


class RunInFlightError(MetsukeError):
    """Raised when a report is fired while one of its runs is still in flight.

    The per-report concurrency lock permits at most one open run per report.
    """

    def __init__(self, name: str, run_id: str | None = None) -> None:
        held = f" (run {run_id})" if run_id else ""
        super().__init__(
            f"Report '{name}' already has a run in flight{held}; not starting a concurrent run."
        )


class RunNotFoundError(MetsukeError):
    """Raised when save_output names a run_id with no open (gathering) run."""

    def __init__(self, run_id: str) -> None:
        super().__init__(
            f"No in-flight run found for run_id: {run_id} (unknown, already completed, or expired)"
        )


class CallbackNotConfiguredError(MetsukeError):
    """Raised when a report fire is requested but no callback target is set."""

    def __init__(self) -> None:
        super().__init__(
            "Report callback is not configured. Set TRENTINA_ALERT_URL and "
            "METSUKE_ALERT_TOKEN so Metsuke can fire the gather callback."
        )


class CallbackDispatchError(MetsukeError):
    """Raised when firing the callback to the alert endpoint fails."""

    def __init__(self, name: str, detail: str) -> None:
        super().__init__(f"Failed to dispatch callback for report '{name}': {detail}")
