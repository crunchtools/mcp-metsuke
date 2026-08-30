"""Pydantic models for mcp-metsuke-crunchtools input validation."""

from __future__ import annotations

from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from pydantic import BaseModel, Field, field_validator

MAX_NAME_LENGTH = 200
MAX_PROMPT_LENGTH = 20000
MAX_TEXT_LENGTH = 2000
MAX_SCHEDULE_LENGTH = 100
MAX_TZ_LENGTH = 64
MAX_PAYLOAD_ITEMS = 2000

Status = Literal["gathering", "ready", "compiled"]


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


class GetOutputParams(BaseModel, extra="forbid"):
    """Parameters for reading a gathered report output."""

    name: str = Field(..., min_length=1, max_length=MAX_NAME_LENGTH)
    gathered_date: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
