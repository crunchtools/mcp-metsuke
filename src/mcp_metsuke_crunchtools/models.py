"""Pydantic models for mcp-metsuke-crunchtools input validation."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal
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


class Finding(BaseModel, extra="forbid"):
    """One gathered finding.

    The fields are declared, not left as a free-form dict, because a model
    calling the tool fills in what the schema names: with a bare ``object``
    item, strict tool-calling models emitted ``{}`` for every finding (RT #1505).
    ``summary`` is the one field every report uses and the one a finding is
    worthless without. The rest are the keys the live reports use; a report
    that needs one more adds it here.

    Example::

        {"summary": "Fedora 45 Beta shipped with Podman 6.",
         "source_url": "https://fedoramagazine.org/...",
         "section": "rss-news-roundup", "theme": "RHEL/Linux"}
    """

    summary: str = Field(
        ...,
        min_length=1,
        max_length=MAX_TEXT_LENGTH,
        description="One or two sentences stating the finding itself.",
    )
    source_url: str | None = Field(
        default=None,
        max_length=MAX_TEXT_LENGTH,
        description="Clickable link to the evidence; null when no source exists.",
    )
    section: str | None = Field(
        default=None,
        max_length=MAX_NAME_LENGTH,
        description="Report section this belongs in, as named by the report's gather prompt.",
    )
    theme: str | None = Field(
        default=None,
        max_length=MAX_NAME_LENGTH,
        description="Grouping within a section, e.g. 'Security' or 'AI/Agentic'.",
    )
    title: str | None = Field(
        default=None,
        max_length=MAX_TEXT_LENGTH,
        description="Headline of the source item, when it has one.",
    )
    category: str | None = Field(
        default=None,
        max_length=MAX_NAME_LENGTH,
        description="Source category, e.g. the feed category the item came from.",
    )
    source_type: str | None = Field(
        default=None,
        max_length=MAX_NAME_LENGTH,
        description="Kind of source, e.g. 'jira', 'slack', 'email', 'rss', 'web'.",
    )
    date: str | None = Field(
        default=None,
        max_length=MAX_NAME_LENGTH,
        description="When it happened, ISO date (YYYY-MM-DD).",
    )
    actors: list[Annotated[str, Field(max_length=MAX_NAME_LENGTH)]] | None = Field(
        default=None,
        max_length=MAX_PAYLOAD_ITEMS,
        description="People or teams involved, by name.",
    )
    outcome_ref: str | None = Field(
        default=None,
        max_length=MAX_TEXT_LENGTH,
        description="Tracker key this finding advances, e.g. a Jira issue key.",
    )

    @field_validator("summary")
    @classmethod
    def _check_summary(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("finding summary must not be blank")
        return value


class SaveOutputParams(BaseModel, extra="forbid"):
    """Parameters for persisting a gathered report output."""

    report_name: str = Field(..., min_length=1, max_length=MAX_NAME_LENGTH)
    payload: list[Finding] = Field(..., max_length=MAX_PAYLOAD_ITEMS)
    window_start: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    window_end: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    status: Status = Field(default="ready")
    gatherer_run_ref: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    run_id: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)

    @field_validator("window_start", "window_end", "gatherer_run_ref", "run_id", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: Any) -> Any:
        """An empty string means "not given".

        Models that fill every optional parameter send ``run_id: ""`` rather
        than omitting it, which read as a lookup for a run named "" and failed
        with "No in-flight run found" (RT #1505).
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    def payload_dicts(self) -> list[dict[str, Any]]:
        """The findings as stored: exactly the keys the caller sent."""
        return [finding.model_dump(exclude_unset=True) for finding in self.payload]


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
