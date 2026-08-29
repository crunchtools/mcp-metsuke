"""Test fixtures for mcp-metsuke-crunchtools."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mcp_metsuke_crunchtools import config as config_mod
from mcp_metsuke_crunchtools import database as db_mod

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Generator


@pytest.fixture(autouse=True)
def _reset_singletons() -> Generator[None]:
    """Reset config and database singletons between tests."""
    config_mod._config = None
    db_mod._db = None
    yield
    if db_mod._db is not None:
        db_mod._db.close()
    db_mod._db = None
    config_mod._config = None


@pytest.fixture
def in_memory_db() -> sqlite3.Connection:
    """Create an in-memory SQLite database with schema applied."""
    return db_mod.get_db(":memory:")
