# Spec 004: Mandatory Sweep, Phase A — Source Readers

> **Status:** Accepted
> **Created:** 2026-11-14
> **Builds on:** the sweep architecture (`sweep/collectors.py`), spec 003

## Overview

Release 3.7.0 adds the deterministic collectors the `rhhi-status` report needs
so its gatherer can stop calling sources itself. The existing collectors answer
"what is waiting on the user"; these answer "what did the user send, say,
write and commit to over a window". They make no judgement and drop nothing
but what the window and the caps exclude.

Wire vocabulary is unchanged (sweep, collector, section). Renaming is a later
major release.

## Collectors

- **`gmail_search`** — one Gmail query (`in:sent after:{window_start}`), every
  matching thread read once, no ownership, noise or age filtering.
  `{window_start}` becomes the read's start as `YYYY/MM/DD`.
- **`slack_search`** — `slack_search_messages` with a query template
  (`from:me after:{date}`), pages of 20 until a short page, the last page,
  `max_messages` or 5 pages. `{date}` is the day before the read's start,
  because Slack's `after:` is exclusive; messages older than the start are
  dropped and counted. Authors resolve to names, one lookup per distinct user.
- **`docs_search`** — Google Docs modified since the start, newest first, via
  workspace-mcp `search_drive_files` (`modifiedTime > '…'`, `file_type:
  document`, `order_by: modifiedTime desc`). `search_docs` matches names only
  and cannot filter by date, so it is not used.
- **`workboard_objectives`** — one `workboard_get_my_objectives_tool` call;
  one record per objective followed by one per key result.

The read's start is the report window's start, or `lookback_days` before the
run when that option is set.

## Changes to existing collectors

- **`calendar_day`** gains `lookback_days` (default 0) and `responses`
  (default: everything but declined). Lookback events are `past_meeting` /
  `past_all_day`; past `needsAction` events are dropped. With both options at
  their defaults the gateway call and every record are unchanged.
- **`jira_issues`** gains `probe_key`. Before the queries it searches
  `key = <probe_key>`; an error or an empty answer (`total: -1`, no issues)
  makes the section `error` with "jira backend returned nothing for known key
  X", and the queries are skipped. A dead connection never reads as "0 new".

## Withholding

Every new collector follows the existing rule: a result Trentina flagged keeps
its ids, links, dates, counts and people as addresses or ids, and withholds
free text — Gmail subject and body, Slack message text, document names,
objective and key-result names.

## Bounds

All calls are sequential. Pages are capped at `MAX_PAGES` (5); records at
`max_threads` (≤200), `max_messages` (≤100), `max_docs` (≤100) and
`MAX_WORKBOARD_RECORDS` (150). Every option model is `extra="forbid"`.

## Acceptance

- Each new collector has fake-gateway tests for the happy path, a flagged
  result, a backend error (section `error`), and the paging or record cap.
- Existing `calendar_day` and `jira_issues` tests pass unchanged.
