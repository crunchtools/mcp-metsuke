"""Pure parsers for the operations sources: Nagios text and Ashigaru's record issues.

Kept apart from ``parsers`` (the briefing sources: Slack, Gmail, Calendar,
Jira) because these feed the weekend report. No I/O, so every rule is testable.
"""

from __future__ import annotations

import re
from typing import Any

# --- Nagios --------------------------------------------------------------

# mcp-nagios returns formatted text. Host and service are split on the FIRST
# " / ": service names carry slashes ("Git Drift /srv"), host names do not.
_NAGIOS_TS = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"
_ALERT_ROW = re.compile(
    rf"^\s+(?P<name>.+?): (?P<events>\d+) problem event\(s\) \((?P<counts>[^;)]*); "
    rf"(?P<hard>\d+) hard\), first (?P<first>{_NAGIOS_TS}) UTC, last (?P<last>{_NAGIOS_TS}) UTC, "
    r"last seen (?P<state>[A-Z]+)(?: \| (?P<output>.*))?$",
    re.M,
)
# Anchored on the header's "), ": the planned-events tail also names a host count.
_ALERT_TOTAL = re.compile(r"\), (?P<total>\d+) host\(s\)/service\(s\)")
_ALERT_MORE = re.compile(r"^\s+\.\.\. and (?P<more>\d+) quieter one\(s\) not shown", re.M)
# mcp-nagios 0.8.0+: events during scheduled downtime are left out of the rows
# above and counted on one closing line instead.
_ALERT_PLANNED = re.compile(
    r"^\s+plus (?P<events>\d+) event\(s\) on (?P<objects>\d+) host\(s\)/service\(s\) "
    r"during scheduled downtime",
    re.M,
)
_STATE_COUNT = re.compile(r"(?P<n>\d+) (?P<state>[A-Z]+)")
_PROBLEM_ROW = re.compile(
    r"^\s+(?P<name>.+?): (?P<state>[A-Z]+) for (?P<duration>[^(]+?) "
    rf"\(since (?P<since>{_NAGIOS_TS}) UTC\), "
    r"(?P<kind>HARD|SOFT) (?P<attempt>\d+/\d+)"
    r"(?P<ack>, acknowledged)?(?P<downtime>, in downtime)?(?: \| (?P<output>.*))?$",
    re.M,
)
_DOWNTIME_ROW = re.compile(
    rf"^\s+(?P<host>\S+) \[(?P<objects>.*?)\]: (?P<start>{_NAGIOS_TS}) UTC \u2192 "
    rf"(?P<end>{_NAGIOS_TS}) UTC \((?P<duration>[^,)]+)(?P<ongoing>, ongoing)?\)"
    r"(?: \| (?P<author>[^:]*?): (?P<comment>.*))?$",
    re.M,
)
_NOTIFY_ROW = re.compile(
    rf"^\s+\[(?P<at>{_NAGIOS_TS}) UTC\] (?P<name>.+?) \u2192 "
    r"(?P<contact>\S+) via \S+: (?P<output>.*)$",
    re.M,
)


def nagios_target(name: str) -> tuple[str, str | None]:
    """``host / service`` as ``(host, service)``; a host check has no service."""
    host, sep, service = name.partition(" / ")
    return host.strip(), (service.strip() if sep else None)


def _target(m: re.Match[str]) -> dict[str, Any]:
    host, service = nagios_target(m["name"])
    return {"host": host, "service": service, "output": (m["output"] or "").strip()}


def nagios_alert_rollup(text: str) -> list[dict[str, Any]]:
    """Rows of ``nagios_alert_history_tool``, noisiest first as the tool lists them."""
    return [
        {
            **_target(m),
            "events": int(m["events"]),
            "counts": {c["state"]: int(c["n"]) for c in _STATE_COUNT.finditer(m["counts"])},
            "hard": int(m["hard"]),
            "first": f"{m['first']}Z",
            "last": f"{m['last']}Z",
            "last_state": m["state"],
        }
        for m in _ALERT_ROW.finditer(text)
    ]


def nagios_rollup_extent(text: str) -> tuple[int | None, int]:
    """How many checks the roll-up covered, and how many quieter ones it did not list."""
    total, more = _ALERT_TOTAL.search(text), _ALERT_MORE.search(text)
    return (int(total["total"]) if total else None), (int(more["more"]) if more else 0)


def nagios_planned_events(text: str) -> tuple[int, int]:
    """Events the roll-up set aside as scheduled downtime, and on how many checks."""
    m = _ALERT_PLANNED.search(text)
    return (int(m["events"]), int(m["objects"])) if m else (0, 0)


def nagios_current_problems(text: str) -> list[dict[str, Any]]:
    """Rows of ``nagios_current_problems_tool``: what is not OK right now."""
    return [
        {
            **_target(m),
            "state": m["state"],
            "duration": m["duration"].strip(),
            "since": f"{m['since']}Z",
            "hard": m["kind"] == "HARD",
            "attempt": m["attempt"],
            "acknowledged": bool(m["ack"]),
            "in_downtime": bool(m["downtime"]),
        }
        for m in _PROBLEM_ROW.finditer(text)
    ]


def nagios_notifications(text: str) -> list[dict[str, Any]]:
    """Rows of ``nagios_notification_history_tool``: what paged, when, and to whom."""
    return [
        {**_target(m), "at": f"{m['at']}Z", "contact": m["contact"]}
        for m in _NOTIFY_ROW.finditer(text)
    ]


def nagios_downtimes(text: str) -> list[dict[str, Any]]:
    """Rows of ``nagios_downtime_history_tool``: each scheduled downtime window.

    ``objects`` lists what the window covered on the host, "host" being the
    host check itself; ``comment`` is everything after the author.
    """
    return [
        {
            "host": m["host"],
            "objects": [o.strip() for o in m["objects"].split("; ") if o.strip()],
            "start": f"{m['start']}Z",
            "end": f"{m['end']}Z",
            "duration": m["duration"].strip(),
            "ongoing": bool(m["ongoing"]),
            "author": (m["author"] or "").strip() or None,
            "comment": (m["comment"] or "").strip(),
        }
        for m in _DOWNTIME_ROW.finditer(text)
    ]


# --- Ashigaru issue bodies -----------------------------------------------

# Ashigaru's hourly sweep keeps #90 (waiting on a maintainer) and #94
# (releases) as Markdown: "## Part (N)" headings over "- " bullets. The fields
# below are Ashigaru's own fixed phrasing; only the backticked title and the
# author login come from outside contributors.
# The constitution's payload cap: no part list, however long, yields more items.
MAX_ASHIGARU_ITEMS = 2000
_PART_HEADING = re.compile(r"^## (?P<name>.+?)(?: \((?P<count>\d+)\))?\s*$")
_ITEM_LINK = re.compile(
    r"^- \[(?P<label>[^\]]+)\]\((?P<url>https://github\.com/[^)\s]+)\)\s*(?P<rest>.*)$"
)
_ITEM_PLAIN = re.compile(r"^- (?P<label>[A-Za-z0-9._-]+): (?P<rest>.*)$")
_ITEM_MORE = re.compile(r"^- and (?P<more>\d+) more\s*$")
_TITLE = re.compile(r"`(?P<title>[^`]*)`")
_DATE = r"\d{4}-\d{2}-\d{2}"
_ITEM_FIELDS = {
    "author": re.compile(r"\bby (?P<v>[A-Za-z0-9][A-Za-z0-9-]{0,38}(?:\[bot\])?),"),
    "opened": re.compile(rf"\bopened (?P<v>{_DATE})"),
    "closed": re.compile(rf"\bClosed (?P<v>{_DATE})"),
    "triaged": re.compile(rf"\bTriaged (?P<v>{_DATE})"),
    "published": re.compile(rf"\bpublished (?P<v>{_DATE})"),
    "last_run": re.compile(rf"\blast run (?P<v>{_DATE})"),
    "recommendation": re.compile(r"\bAshigaru: (?P<v>[a-z][a-z -]{0,40})\."),
    "labels": re.compile(
        r"(?:Triaged \d{4}-\d{2}-\d{2}: |opened \d{4}-\d{2}-\d{2}\. )"
        r"(?P<v>[a-z][a-z0-9 ,:_-]{0,80})\.$"
    ),
    "entries": re.compile(r"^(?P<v>\d+) entr(?:y|ies)"),
    "since": re.compile(r"\bsince v?(?P<v>\d+\.\d+\.\d+)"),
    "release": re.compile(r"^release v?(?P<v>\d+\.\d+\.\d+)"),
    "conclusion": re.compile(r"\blast run \d{4}-\d{2}-\d{2}: (?P<v>[a-z_]+)\."),
}
_REPO_NUMBER = re.compile(r"^(?P<repo>[A-Za-z0-9._-]+)#(?P<number>\d+)$")
_REPO_VERSION = re.compile(r"^(?P<repo>[A-Za-z0-9._-]+) v?(?P<version>\d+\.\d+\.\d+\S*)$")


def _item_fields(rest: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for name, pattern in _ITEM_FIELDS.items():
        if m := pattern.search(rest):
            value = m["v"]
            fields[name] = int(value) if name == "entries" else value.strip()
    return fields


def ashigaru_parts(body: str) -> dict[str, dict[str, Any]]:
    """An Ashigaru issue body as ``{part name: {count, more, items}}``.

    Each item keeps the link Ashigaru wrote (``label``, ``url``), the repo and
    number or version parsed from the label, the backticked ``title``, and the
    fixed-phrase fields (author, dates, recommendation, labels). ``count`` is
    the part's own total from its heading; ``more`` is its "and N more" tail.
    At most ``MAX_ASHIGARU_ITEMS`` items are kept across all parts.
    """
    parts: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    seen = 0
    for line in body.splitlines():
        if m := _PART_HEADING.match(line):
            current = {"count": int(m["count"]) if m["count"] else None, "more": 0, "items": []}
            parts[m["name"].strip()] = current
            continue
        if current is None:
            continue
        if m := _ITEM_MORE.match(line):
            current["more"] = int(m["more"])
            continue
        m = _ITEM_LINK.match(line) or _ITEM_PLAIN.match(line)
        if not m or seen >= MAX_ASHIGARU_ITEMS:
            continue
        seen += 1
        rest = m["rest"]
        title = _TITLE.search(rest)
        item: dict[str, Any] = {
            "label": m["label"],
            "url": m.groupdict().get("url"),
            "title": title["title"].strip() if title else None,
            **_item_fields(_TITLE.sub("", rest).strip()),
        }
        if rn := _REPO_NUMBER.match(m["label"]):
            item["repo"], item["number"] = rn["repo"], int(rn["number"])
        elif rv := _REPO_VERSION.match(m["label"]):
            item["repo"], item["version"] = rv["repo"], rv["version"]
        else:
            item["repo"] = m["label"]
        current["items"].append(item)
    return parts


def semver(value: str | None) -> tuple[int, int, int, int] | None:
    """``"v1.2.3"`` as ``(1, 2, 3, 1)``; a prerelease (``"1.2.3-rc1"``) ends in 0, so
    it sorts below its own final release. None when it is not a version."""
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)(-)?", (value or "").strip())
    return (int(m[1]), int(m[2]), int(m[3]), 0 if m[4] else 1) if m else None
