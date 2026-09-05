"""Configuration for mcp-metsuke-crunchtools."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import SecretStr

_config: Config | None = None

DEFAULT_POLL_SECONDS = 60
DEFAULT_RUN_LOCK_TTL_SECONDS = 1800


class Config:
    """Metsuke configuration from environment variables.

    Metsuke holds no third-party API credentials. It does, however, own its
    report schedule: a built-in scheduler fires due reports by POSTing to the
    Trentina alert endpoint, which HMAC-signs and forwards the callback to the
    owning gatherer agent. The alert token is the one secret Metsuke carries,
    typed as SecretStr and honoring the ``<VAR>_FILE`` container convention.
    """

    def __init__(self) -> None:
        default_db = str(Path.home() / ".local" / "share" / "mcp-metsuke" / "metsuke.db")
        self.db_path: str = _read_env("METSUKE_DB", default_db)

        self.trentina_alert_url: str = _read_env("TRENTINA_ALERT_URL", "").rstrip("/")
        token = _read_env("METSUKE_ALERT_TOKEN", "")
        self.alert_token: SecretStr | None = SecretStr(token) if token else None

        try:
            self.scheduler_poll_seconds: int = int(
                _read_env("METSUKE_SCHEDULER_POLL_SECONDS", str(DEFAULT_POLL_SECONDS))
            )
        except ValueError:
            self.scheduler_poll_seconds = DEFAULT_POLL_SECONDS

        try:
            self.run_lock_ttl_seconds: int = int(
                _read_env("METSUKE_RUN_LOCK_TTL_SECONDS", str(DEFAULT_RUN_LOCK_TTL_SECONDS))
            )
        except ValueError:
            self.run_lock_ttl_seconds = DEFAULT_RUN_LOCK_TTL_SECONDS

        self._scheduler_enabled_override: bool | None = _read_bool("METSUKE_SCHEDULER_ENABLED")

    @property
    def callback_configured(self) -> bool:
        """True when the alert URL and token needed to fire a callback are set."""
        return bool(self.trentina_alert_url and self.alert_token)

    @property
    def scheduler_enabled(self) -> bool:
        """Whether the background scheduler should run.

        Explicit ``METSUKE_SCHEDULER_ENABLED`` wins; otherwise the scheduler
        runs only when a callback is actually configured (so stdio/dev sessions
        and tests stay passive).
        """
        if self._scheduler_enabled_override is not None:
            return self._scheduler_enabled_override
        return self.callback_configured

    def ensure_db_dir(self) -> None:
        """Create the database directory if it does not exist."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)


def _read_env(name: str, default: str) -> str:
    """Read an env var, honoring the ``<VAR>_FILE`` container convention.

    A ``<VAR>_FILE`` path (if set and readable) takes precedence over the plain
    variable; its contents are stripped. This mirrors the secret-file pattern
    required by the MCP Server profile.
    """
    file_var = os.environ.get(f"{name}_FILE")
    if file_var:
        contents = Path(file_var).read_text(encoding="utf-8").strip()
        if contents:
            return contents
    return os.environ.get(name, default)


def _read_bool(name: str) -> bool | None:
    """Read a tri-state boolean env var: True, False, or None (unset/unknown)."""
    raw = os.environ.get(name, "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return None


def get_config() -> Config:
    """Get or create the singleton configuration."""
    global _config
    if _config is None:
        _config = Config()
    return _config
