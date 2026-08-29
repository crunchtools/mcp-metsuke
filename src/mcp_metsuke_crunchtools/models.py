"""Pydantic models for mcp-metsuke-crunchtools input validation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

MAX_NAME_LENGTH = 200
MAX_PROMPT_LENGTH = 20000
MAX_TEXT_LENGTH = 2000
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
    schedule: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    source_config: dict[str, Any] | None = Field(default=None)


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
