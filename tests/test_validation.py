"""Pydantic model validation tests for mcp-metsuke-crunchtools."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mcp_metsuke_crunchtools.models import (
    MAX_NAME_LENGTH,
    GetOutputParams,
    GetSpecParams,
    SaveOutputParams,
    UpsertDefinitionParams,
)


class TestGetSpecParams:
    def test_valid(self) -> None:
        assert GetSpecParams(name="core-platform-status").name == "core-platform-status"

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GetSpecParams(name="")

    def test_too_long_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GetSpecParams(name="x" * (MAX_NAME_LENGTH + 1))

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GetSpecParams(name="ok", extra="nope")  # type: ignore[call-arg]


class TestUpsertDefinitionParams:
    def test_minimal(self) -> None:
        params = UpsertDefinitionParams(name="r", gather_prompt="do it")
        assert params.owner_agent == "kagetora"
        assert params.source_config is None

    def test_full(self) -> None:
        params = UpsertDefinitionParams(
            name="r",
            gather_prompt="do it",
            owner_agent="takeda",
            schedule="0 6 * * 5",
            timezone="America/New_York",
            source_config={"sources": ["gmail"]},
        )
        assert params.owner_agent == "takeda"
        assert params.timezone == "America/New_York"
        assert params.source_config == {"sources": ["gmail"]}

    def test_default_timezone(self) -> None:
        params = UpsertDefinitionParams(name="r", gather_prompt="do it")
        assert params.timezone == "UTC"

    def test_empty_prompt_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UpsertDefinitionParams(name="r", gather_prompt="")

    def test_invalid_cron_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UpsertDefinitionParams(name="r", gather_prompt="x", schedule="not a cron")

    def test_empty_schedule_normalized_to_none(self) -> None:
        params = UpsertDefinitionParams(name="r", gather_prompt="x", schedule="")
        assert params.schedule is None

    def test_invalid_timezone_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UpsertDefinitionParams(name="r", gather_prompt="x", timezone="Mars/Phobos")

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UpsertDefinitionParams(name="r", gather_prompt="x", junk=1)  # type: ignore[call-arg]


class TestSaveOutputParams:
    def test_valid(self) -> None:
        params = SaveOutputParams(report_name="r", payload=[{"claim": "x", "source": "http://e"}])
        assert params.status == "ready"

    def test_bad_status_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SaveOutputParams(report_name="r", payload=[], status="bogus")  # type: ignore[arg-type]

    def test_valid_statuses(self) -> None:
        for status in ("gathering", "ready", "compiled"):
            params = SaveOutputParams(report_name="r", payload=[], status=status)  # type: ignore[arg-type]
            assert params.status == status


class TestGetOutputParams:
    def test_minimal(self) -> None:
        assert GetOutputParams(name="r").gathered_date is None

    def test_with_date(self) -> None:
        assert GetOutputParams(name="r", gathered_date="2026-08-29").gathered_date == "2026-08-29"

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GetOutputParams(name="r", nope=1)  # type: ignore[call-arg]
