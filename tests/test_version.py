"""Every place that carries the release version agrees with pyproject.toml."""

import json
import re
import tomllib
from pathlib import Path

from mcp_metsuke_crunchtools import __version__

ROOT = Path(__file__).resolve().parent.parent
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def test_package_version() -> None:
    assert __version__ == VERSION


def test_server_json_versions() -> None:
    server = json.loads((ROOT / "server.json").read_text())
    assert server["version"] == VERSION
    assert {p["version"] for p in server["packages"]} == {VERSION}


def test_container_label_version() -> None:
    label = re.search(r'^\s*version="([^"]+)"', (ROOT / "Containerfile").read_text(), re.M)
    assert label is not None
    assert label.group(1) == VERSION
