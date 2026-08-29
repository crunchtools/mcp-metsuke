"""Configuration for mcp-metsuke-crunchtools."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import SecretStr

_config: Config | None = None


class Config:
    """Metsuke configuration from environment variables.

    No API credentials are required — Metsuke is a self-contained local
    SQLite service reached through the Trentina gateway. The _api_token field
    is typed as SecretStr | None for constitution compliance and future
    extensibility.
    """

    _api_token: SecretStr | None = None

    def __init__(self) -> None:
        default_db = str(Path.home() / ".local" / "share" / "mcp-metsuke" / "metsuke.db")
        self.db_path: str = _read_env("METSUKE_DB", default_db)

    def ensure_db_dir(self) -> None:
        """Create the database directory if it does not exist."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)


def _read_env(name: str, default: str) -> str:
    """Read an env var, honoring the ``<VAR>_FILE`` container convention.

    A ``<VAR>_FILE`` path (if set and readable) takes precedence over the plain
    variable; its contents are stripped. This mirrors the secret-file pattern
    required by the MCP Server profile even though Metsuke holds no secrets.
    """
    file_var = os.environ.get(f"{name}_FILE")
    if file_var:
        contents = Path(file_var).read_text(encoding="utf-8").strip()
        if contents:
            return contents
    return os.environ.get(name, default)


def get_config() -> Config:
    """Get or create the singleton configuration."""
    global _config
    if _config is None:
        _config = Config()
    return _config
