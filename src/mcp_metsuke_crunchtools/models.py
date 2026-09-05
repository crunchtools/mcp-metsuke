"""Pydantic models for mcp-metsuke-crunchtools input validation."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from pydantic import BaseModel, Field, field_validator, model_validator

MAX_NAME_LENGTH = 200
MAX_PROMPT_LENGTH = 20000
MAX_TEXT_LENGTH = 2000
MAX_SCHEDULE_LENGTH = 100
MAX_TZ_LENGTH = 64
MAX_PAYLOAD_ITEMS = 2000
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 500

Status = Literal["gathering", "ready", "compiled", "failed"]


def _is_iso_date(value: str) -> bool:
    """True when value is a valid YYYY-MM-DD calendar date."""
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


class GetSpecParams(BaseModel, extra="forbid"):
    """Parameters for fetching a report definition's gather spec."""

    name: str = Field(..., min_length=1, max_length=MAX_NAME_LENGTH)


class UpsertDefinitionParams(BaseModel, extra="forbid"):
    """Parameters for creating or updating a report definition."""

    name: str = Field(..., min_length=1, max_length=MAX_NAME_LENGTH)
    gather_prompt: str = Field(..., min_length=1, max_length=MAX_PROMPT_LENGTH)
    owner_agent: str = Field(default="kagetora", min_length=1, max_length=MAX_NAME_LENGTH)
    schedule: str | None = Field(default=None, max_length=MAX_SCHEDULE_LENGTH)
    timezone: str = Field(default="UTC", min_length=1, max_length=MAX_TZ_LENGTH)
    source_config: dict[str, Any] | None = Field(default=None)

    @field_validator("schedule")
    @classmethod
    def _check_schedule(cls, value: str | None) -> str | None:
        if value and not croniter.is_valid(value):
            raise ValueError(
                f"schedule must be a valid cron expression (e.g. '0 6 * * 5'), got: {value!r}"
            )
        return value or None

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(
                f"timezone must be a valid IANA zone (e.g. 'America/New_York'), got: {value!r}"
            ) from exc
        return value


class TriggerReportParams(BaseModel, extra="forbid"):
    """Parameters for manually firing a report gather now."""

    name: str = Field(..., min_length=1, max_length=MAX_NAME_LENGTH)


class SaveOutputParams(BaseModel, extra="forbid"):
    """Parameters for persisting a gathered report output."""

    report_name: str = Field(..., min_length=1, max_length=MAX_NAME_LENGTH)
    payload: list[dict[str, Any]] = Field(..., max_length=MAX_PAYLOAD_ITEMS)
    window_start: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    window_end: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    status: Status = Field(default="ready")
    gatherer_run_ref: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    run_id: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)


class GetOutputParams(BaseModel, extra="forbid"):
    """Parameters for reading a gathered report output."""

    name: str = Field(..., min_length=1, max_length=MAX_NAME_LENGTH)
    gathered_date: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)


class ListOutputsParams(BaseModel, extra="forbid"):
    """Parameters for browsing saved-output metadata (no payloads)."""

    report_name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME_LENGTH)
    limit: int = Field(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT)


class DeleteOutputParams(BaseModel, extra="forbid"):
    """Parameters for deleting one saved output by id."""

    output_id: int = Field(..., ge=1)


class PruneOutputsParams(BaseModel, extra="forbid"):
    """Parameters for bulk-pruning a report's saved outputs.

    Exactly one of ``keep_last`` (retain the N newest) or ``before_date``
    (drop everything gathered before that date) must be given.
    """

    report_name: str = Field(..., min_length=1, max_length=MAX_NAME_LENGTH)
    keep_last: int | None = Field(default=None, ge=0)
    before_date: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)

    @field_validator("before_date")
    @classmethod
    def _check_before_date(cls, value: str | None) -> str | None:
        if value and not _is_iso_date(value):
            raise ValueError(f"before_date must be YYYY-MM-DD, got: {value!r}")
        return value

    @model_validator(mode="after")
    def _exactly_one_criterion(self) -> PruneOutputsParams:
        if (self.keep_last is None) == (self.before_date is None):
            raise ValueError("provide exactly one of keep_last or before_date")
        return self
