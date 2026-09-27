"""The sweep runner: execute a definition's steps in order, record every outcome.

A definition opts in with ``source_config.sweep``::

    {"timezone": "America/New_York", "window_hour": 6,
     "steps": [{"section": "slack", "collector": "slack_waiting",
                "options": {"user_id": "U…", "handle": "…"}}, …]}

Steps run strictly one after another. A collector that raises is recorded as an
``error`` section and the sweep moves on; the overall status is ``ready`` when
every section is ok, ``partial`` when some are not, ``error`` when none are.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, field_validator, model_validator

from .collectors import COLLECTORS, MAX_TZ_LENGTH, OPTION_MODELS, CollectorName, valid_zone
from .window import previous_weekday_at

if TYPE_CHECKING:
    from .client import Gateway

logger = logging.getLogger("mcp_metsuke.sweep")

MAX_STEPS = 12
LAST_HOUR = 23


class SweepStep(BaseModel, extra="forbid"):
    """One step: a named collector writing one section."""

    section: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    collector: CollectorName
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_options(self) -> SweepStep:
        OPTION_MODELS[self.collector](**self.options)
        return self


class SweepSpec(BaseModel, extra="forbid"):
    """The ``source_config.sweep`` block of a definition."""

    timezone: str = Field(default="America/New_York", min_length=1, max_length=MAX_TZ_LENGTH)
    window_hour: int = Field(default=6, ge=0, le=LAST_HOUR)
    steps: list[SweepStep] = Field(..., min_length=1, max_length=MAX_STEPS)

    _check_timezone = field_validator("timezone")(valid_zone)

    @field_validator("steps")
    @classmethod
    def _unique_sections(cls, steps: list[SweepStep]) -> list[SweepStep]:
        names = [s.section for s in steps]
        if len(names) != len(set(names)):
            raise ValueError("sweep step sections must be unique")
        return steps


def sweep_spec_of(source_config: dict[str, Any] | None) -> SweepSpec | None:
    """The validated sweep spec in a source_config, or None when there is no key.

    A present-but-empty ``sweep`` is invalid, not absent: it raises, so a
    definition cannot silently lose its sweep.
    """
    if not source_config or "sweep" not in source_config:
        return None
    return SweepSpec.model_validate(source_config["sweep"])


async def run_sweep(spec: SweepSpec, gw: Gateway, now: datetime | None = None) -> dict[str, Any]:
    """Run every step in order and return the sweep document stored on the run.

    Args:
        spec: The validated sweep spec from the definition.
        gw: An open gateway; calls are made one at a time.
        now: The instant to compute the window from. Defaults to the current
            time; tests pass a fixed instant.

    Returns:
        ``{status, generated_at, window: {start, end}, sections}``. ``status``
        is ``ready`` when every section is ok, ``error`` when every section
        failed, else ``partial``. Each section is
        ``{collector, status, errors, stats, records}`` as built by its
        collector; a collector that raises becomes an ``error`` section.
    """
    now = now or datetime.now(UTC)
    window = previous_weekday_at(now, spec.timezone, spec.window_hour)
    sections: dict[str, Any] = {}
    for step in spec.steps:
        collector = COLLECTORS[step.collector]
        try:
            sections[step.section] = await collector(gw, window, now, step.options)
        except Exception as exc:
            logger.exception("sweep step '%s' crashed", step.section)
            sections[step.section] = {
                "collector": step.collector,
                "status": "error",
                "errors": [f"{type(exc).__name__}: {exc}"[:500]],
                "stats": {},
                "records": [],
            }
    statuses = {s["status"] for s in sections.values()}
    if statuses == {"ok"}:
        overall = "ready"
    elif statuses == {"error"}:
        overall = "error"
    else:
        overall = "partial"
    return {
        "status": overall,
        "generated_at": now.astimezone(UTC).isoformat(),
        "window": window.as_dict(),
        "sections": sections,
    }
