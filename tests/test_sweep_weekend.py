"""Tests for the weekend report's collectors: Nagios health, Ashigaru records, releases.

The fixtures are trimmed from the real tool output of 2026-10-24, the day the
weekend report went out garbled: Trentina refused #90 and the feed lists, and
the gathering model guessed at what it could not read.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any, ClassVar

from mcp_metsuke_crunchtools.sweep import SweepSpec, ops_parsers, run_sweep
from mcp_metsuke_crunchtools.sweep.client import GatewayResult

NOW = datetime(2026, 10, 24, 23, 0, tzinfo=UTC)

ROLLUP = """Problem alerts (last 168h), 222 host(s)/service(s), noisiest first:
  lotor.dc3.crunchtools.com / Host memory usage: 71 problem event(s) (3 CRITICAL, 68 WARNING; 18 hard), first 2026-10-20 13:22:04 UTC, last 2026-10-24 22:45:07 UTC, last seen WARNING | WARNING - Memory 86% used [13858MB/15986MB]
  lotor.dc3.crunchtools.com / Git Drift /srv: 34 problem event(s) (1 CRITICAL, 33 WARNING; 11 hard), first 2026-10-18 07:05:20 UTC, last 2026-10-24 15:25:06 UTC, last seen OK | (No output on stdout) stderr: connect to address 10.88.0.1 port 5666: Connection refused
  ctr-postiz.crunchtools.com: 8 problem event(s) (8 DOWN; 0 hard), first 2026-10-18 19:15:22 UTC, last 2026-10-24 19:39:56 UTC, last seen UP | CRITICAL - Container postiz.crunchtools.com is running
  ctr-crunchtools.com / HTTPS: 4 problem event(s) (4 CRITICAL; 1 hard), first 2026-10-18 05:02:03 UTC, last 2026-10-24 19:54:01 UTC, last seen OK | CRITICAL - Socket timeout
  ctr-educatedconfusion.com / HTTPS: 3 problem event(s) (3 WARNING; 0 hard), first 2026-10-18 14:42:49 UTC, last 2026-10-22 02:46:39 UTC, last seen OK | HTTP WARNING
  ... and 182 quieter one(s) not shown
"""  # noqa: E501
PROBLEMS = """=== Service Problems ===
  lotor.dc3.crunchtools.com / Host failed systemd units: CRITICAL for 5h 3m (since 2026-10-24 19:07:46 UTC), HARD 3/3, acknowledged | SYSTEMD UNITS CRITICAL - 2 failed units: mcp-trove-backfill.service
  lotor.dc3.crunchtools.com / Host memory usage: WARNING for 1h 25m (since 2026-10-24 22:45:07 UTC), HARD 3/3 | WARNING - Memory 86% used [13897MB/15986MB]

Total: 0 host(s), 2 service(s)
"""  # noqa: E501
PAGES = """Notifications (last 168h):
  [2026-10-24 09:00:54 UTC] lotor.dc3.crunchtools.com / Host failed systemd units → hermes via notify-hermes-service: SYSTEMD UNITS CRITICAL - 1 failed units: podman-auto-update.service
  [2026-10-18 16:39:00 UTC] ctr-openclaw.crunchtools.com → hermes via notify-hermes-host: CRITICAL - Container openclaw.crunchtools.com not found
"""  # noqa: E501

INBOX = """<!-- ashigaru:inbox -->
What is waiting on a maintainer across the organization.

## Pull requests from outside contributors (1)

- [memory#2](https://github.com/crunchtools/memory/pull/2) `feat: Add multi-arch (amd64 + arm64) container builds`, by marrusl, opened 2026-03-30. Ashigaru: request changes.

## Issues from outside contributors (1)

- [mcp-gemini#2](https://github.com/crunchtools/mcp-gemini/issues/2) `API keys stored in plaintext in MCP config when using --env GEMINI_API_KEY`, by Amy-Ra-lph, opened 2026-04-16. enhancement, triaged.

## Pull requests Ashigaru handed over (0)

Nothing waiting.

## Fixed by Ashigaru, past 7 days (2)

- [trentina#472](https://github.com/crunchtools/trentina/issues/472) `Audit: hash-chain gateway_calls rows`, opened 2026-10-17. Closed 2026-10-22.
- [mcp-nagios#2](https://github.com/crunchtools/mcp-nagios/issues/2) `Make CGI path prefix configurable`, opened 2026-08-24. Closed 2026-10-21.

## Triaged, past 7 days, still open (1)

- [rotv#811](https://github.com/crunchtools/rotv/issues/811) `Roadmap: what signed-in visitors get`, opened 2026-10-21. Triaged 2026-10-21: enhancement.

## Red on the default branch (1)

- [constitution](https://github.com/crunchtools/constitution/actions/runs/39099083150) `fleet-drift.yml`, last run 2026-10-18: failure.
"""  # noqa: E501
RELEASES = """<!-- ashigaru:releases -->
## Published in the last seven days (5)

- [mcp-workboard v0.15.0](https://github.com/crunchtools/mcp-workboard/releases/tag/v0.15.0), published 2026-10-20.
- [mcp-workboard v0.11.2](https://github.com/crunchtools/mcp-workboard/releases/tag/v0.11.2), published 2026-10-17.
- [mcp-github v1.6.0](https://github.com/crunchtools/mcp-github/releases/tag/v1.6.0), published 2026-10-20.
- [trentina v1.16.0](https://github.com/crunchtools/trentina/releases/tag/v1.16.0), published 2026-10-17.
- [tig v0.4.0](https://github.com/crunchtools/tig/releases/tag/v0.4.0), published 2026-10-19.
- and 2 more

## Release pull requests open (1)

- [mcp-metsuke#58](https://github.com/crunchtools/mcp-metsuke/pull/58) release 3.4.0. Merges when its checks pass.

## Merged and not released (1)

- rotv: 39 entries since 1.38.4. Release mode off.

## Could not release (0)

No refusals.
"""  # noqa: E501


def _ctr(name: str, source: str, version: str) -> dict[str, Any]:
    return {
        "Names": [name],
        "Image": f"quay.io/crunchtools/{name}:latest",
        "State": "running",
        "Labels": {
            "org.opencontainers.image.source": source,
            "org.opencontainers.image.version": version,
        },
    }


CONTAINERS = {
    "items": [
        _ctr("mcp-workboard", "https://github.com/crunchtools/mcp-workboard", "0.11.2"),
        _ctr("mcp-github", "https://github.com/crunchtools/mcp-github", "1.6.0"),
        _ctr("trentina", "https://github.com/crunchtools/trentina", "1.23.0"),
    ]
}


def _issues(body: str, *extra: int) -> str:
    issues = [{"number": n, "html_url": f"https://github.com/crunchtools/ashigaru/issues/{n}",
               "body": "stale", "updated_at": "2026-10-21T10:06:10Z"} for n in extra]  # fmt: skip
    issues.append({"number": 90, "html_url": "https://github.com/crunchtools/ashigaru/issues/90",
                   "body": body, "updated_at": "2026-10-24T19:07:36Z"})  # fmt: skip
    return json.dumps({"items": issues})


class Gateway:
    def __init__(self, answers: dict[str, GatewayResult]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def call(self, backend: str, tool: str, args: dict[str, Any]) -> GatewayResult:
        self.calls.append((backend, tool, args))
        return self.answers[tool]


def _sweep(step: dict[str, Any], gw: Gateway) -> dict[str, Any]:
    spec = SweepSpec(steps=[step])
    return asyncio.run(run_sweep(spec, gw, now=NOW))["sections"][step["section"]]


class TestNagiosParsers:
    def test_rollup_rows_totals_and_the_quieter_tail(self) -> None:
        rows = ops_parsers.nagios_alert_rollup(ROLLUP)
        assert ops_parsers.nagios_rollup_extent(ROLLUP) == (222, 182)
        assert len(rows) == 5
        assert rows[1]["service"] == "Git Drift /srv"
        assert rows[0]["counts"] == {"CRITICAL": 3, "WARNING": 68}
        assert rows[2]["service"] is None
        assert rows[2]["counts"] == {"DOWN": 8}

    def test_current_problems_and_acknowledgement(self) -> None:
        rows = ops_parsers.nagios_current_problems(PROBLEMS)
        assert [r["acknowledged"] for r in rows] == [True, False]
        assert rows[1]["output"].endswith("[13897MB/15986MB]")

    def test_notifications(self) -> None:
        rows = ops_parsers.nagios_notifications(PAGES)
        assert rows[1] == {
            "host": "ctr-openclaw.crunchtools.com",
            "service": None,
            "at": "2026-10-18 16:39:00Z",
            "contact": "hermes",
            "output": "CRITICAL - Container openclaw.crunchtools.com not found",
        }


class TestAshigaruPartsCap:
    def test_items_are_capped_across_parts(self) -> None:
        line = "- [r#1](https://github.com/crunchtools/r/issues/1) `t`, opened 2026-10-01."
        body = (
            "## A (3000)\n"
            + "\n".join([line] * 1500)
            + "\n## B (1500)\n"
            + "\n".join([line] * 1500)
        )
        parts = ops_parsers.ashigaru_parts(body)
        assert sum(len(p["items"]) for p in parts.values()) == ops_parsers.MAX_ASHIGARU_ITEMS


class TestSemver:
    def test_a_prerelease_sorts_below_its_final_release(self) -> None:
        assert ops_parsers.semver("1.2.3-rc1") < ops_parsers.semver("v1.2.3")
        assert ops_parsers.semver("1.2.3") < ops_parsers.semver("1.2.4-rc1")
        assert ops_parsers.semver("latest") is None


class TestNagiosHealth:
    STEP: ClassVar[dict[str, Any]] = {"section": "infra", "collector": "nagios_health"}

    def _gw(self, flagged: bool = False) -> Gateway:
        return Gateway(
            {
                "nagios_current_problems_tool": GatewayResult(text=PROBLEMS),
                "nagios_alert_history_tool": GatewayResult(text=ROLLUP, flagged=flagged),
                "nagios_notification_history_tool": GatewayResult(text=PAGES),
            }
        )

    def test_qualifying_checks_open_first_with_links_and_local_times(self) -> None:
        section = _sweep(self.STEP, self._gw())
        names = [(r["host"], r["service"]) for r in section["records"]]
        assert names[:2] == [
            ("lotor.dc3.crunchtools.com", "Host memory usage"),
            ("lotor.dc3.crunchtools.com", "Host failed systemd units"),
        ]
        # A few events do not qualify, even with a hard one; a page does.
        assert ("ctr-crunchtools.com", "HTTPS") not in names
        assert ("ctr-educatedconfusion.com", "HTTPS") not in names
        assert ("ctr-openclaw.crunchtools.com", None) in names
        drift = next(r for r in section["records"] if r["service"] == "Git Drift /srv")
        assert drift["status_url"].endswith("service=Git%20Drift%20%2Fsrv")
        assert drift["first"] == "Oct 18 3:05 AM"
        units = section["records"][1]
        assert units["acknowledged"]
        assert units["pages"] == 1
        assert units["since"] == "Oct 24 3:07 PM"
        assert section["stats"]["rollup_more"] == 182
        assert section["status"] == "ok"

    def test_a_failed_source_leaves_the_others(self) -> None:
        gw = self._gw()
        gw.answers["nagios_alert_history_tool"] = GatewayResult(text="", error="timeout")
        section = _sweep(self.STEP, gw)
        assert section["status"] == "partial"
        assert section["records"][0]["open_now"]

    def test_flagged_rollup_keeps_counts_and_withholds_output(self) -> None:
        section = _sweep(self.STEP, self._gw(flagged=True))
        drift = next(r for r in section["records"] if r["service"] == "Git Drift /srv")
        assert drift["events"] == 34
        assert drift["last_output"] is None


class TestAshigaruIssue:
    STEP: ClassVar[dict[str, Any]] = {
        "section": "inbox",
        "collector": "ashigaru_issue",
        "options": {"label": "ashigaru-inbox"},
    }

    def test_found_by_label_oldest_issue_parsed_into_parts(self) -> None:
        gw = Gateway({"list_issues_tool": GatewayResult(text=_issues(INBOX, 115))})
        section = _sweep(self.STEP, gw)
        assert gw.calls[0][2]["labels"] == "ashigaru-inbox"
        assert section["stats"]["issue_number"] == 90
        parts = section["stats"]["parts"]
        assert parts["Fixed by Ashigaru, past 7 days"] == {"count": 2, "listed": 2, "more": 0}
        outside = section["records"][0]
        assert outside["repo"] == "memory"
        assert outside["number"] == 2
        assert outside["author"] == "marrusl"
        assert outside["recommendation"] == "request changes"
        red = section["records"][-1]
        assert red["title"] == "fleet-drift.yml"
        assert red["conclusion"] == "failure"

    def test_the_record_cap_is_reflected_in_part_stats(self) -> None:
        line = "- [r#1](https://github.com/crunchtools/r/issues/1) `t`, opened 2026-10-01."
        body = "## Fixed by Ashigaru, past 7 days (130)\n" + "\n".join([line] * 130)
        gw = Gateway({"list_issues_tool": GatewayResult(text=_issues(body))})
        section = _sweep(self.STEP, gw)
        stats = section["stats"]["parts"]["Fixed by Ashigaru, past 7 days"]
        assert stats == {"count": 130, "listed": 100, "more": 30}
        assert len(section["records"]) == 100

    def test_flagged_issue_withholds_titles_only(self) -> None:
        gw = Gateway({"list_issues_tool": GatewayResult(text=_issues(INBOX), flagged=True)})
        section = _sweep(self.STEP, gw)
        assert all(r["title"] is None for r in section["records"])
        assert section["records"][0]["url"] == "https://github.com/crunchtools/memory/pull/2"

    def test_a_failed_listing_is_recorded(self) -> None:
        gw = Gateway({"list_issues_tool": GatewayResult(text="", error="403 Forbidden")})
        section = _sweep(self.STEP, gw)
        assert section["status"] == "error"
        assert section["errors"] == ["label ashigaru-inbox: 403 Forbidden"]

    def test_an_unparseable_listing_is_recorded_without_quoting_it(self) -> None:
        gw = Gateway({"list_issues_tool": GatewayResult(text="<html>not json")})
        section = _sweep(self.STEP, gw)
        assert section["status"] == "partial"
        assert "unparseable result" in section["errors"][0]
        assert "html" not in section["errors"][0]

    def test_no_open_issue_is_an_error_section(self) -> None:
        gw = Gateway({"list_issues_tool": GatewayResult(text=json.dumps({"items": []}))})
        assert _sweep(self.STEP, gw)["status"] == "partial"


class TestCrunchtoolsReleases:
    STEP: ClassVar[dict[str, Any]] = {"section": "shipped", "collector": "crunchtools_releases"}

    def _gw(self, podman: GatewayResult) -> Gateway:
        return Gateway(
            {
                "list_issues_tool": GatewayResult(text=_issues(RELEASES)),
                "container_list_tool": podman,
            }
        )

    def test_newest_per_repo_compared_with_what_lotor_runs(self) -> None:
        section = _sweep(self.STEP, self._gw(GatewayResult(text=json.dumps(CONTAINERS))))
        repos = {r["repo"]: r for r in section["records"] if r["kind"] == "repo"}
        assert repos["mcp-workboard"]["newest"] == "0.15.0"
        assert repos["mcp-workboard"]["releases"] == 2
        assert repos["mcp-workboard"]["status"] == "behind"
        assert repos["mcp-github"]["status"] == "live"
        # Running past the newest GitHub release (release mode off) is live, not a mismatch.
        assert repos["trentina"]["status"] == "live"
        assert repos["tig"]["status"] == "not-deployed"
        assert section["stats"]["parts"]["Published in the last seven days"]["more"] == 2
        assert (section["stats"]["behind"], section["stats"]["live"]) == (1, 2)
        pr = next(r for r in section["records"] if r["part"] == "Release pull requests open")
        assert (pr["repo"], pr["number"], pr["release"]) == ("mcp-metsuke", 58, "3.4.0")
        unreleased = next(r for r in section["records"] if r["part"] == "Merged and not released")
        assert (unreleased["entries"], unreleased["since"]) == (39, "1.38.4")

    def test_flagged_releases_issue_withholds_titles(self) -> None:
        gw = Gateway(
            {
                "list_issues_tool": GatewayResult(text=_issues(RELEASES), flagged=True),
                "container_list_tool": GatewayResult(text=json.dumps(CONTAINERS)),
            }
        )
        section = _sweep(self.STEP, gw)
        assert section["stats"]["flagged"]
        assert all(r.get("title") is None for r in section["records"])

    def test_podman_failure_drops_only_the_deploy_status(self) -> None:
        section = _sweep(self.STEP, self._gw(GatewayResult(text="", error="timeout")))
        repo = next(r for r in section["records"] if r["kind"] == "repo")
        assert "status" not in repo
        assert section["status"] == "partial"
