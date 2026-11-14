# Implementation Plan: Mandatory Sweep, Phase A

> **Spec ID:** 004-mandatory-sweep
> **Status:** Complete
> **Last Updated:** 2026-11-14

## Summary

Four collectors and two options added to `sweep/collectors.py`, two
text parsers to `sweep/parsers.py`, all registered in `COLLECTORS`,
`OPTION_MODELS` and `CollectorName`.

## Steps

1. Generalize `_search_threads` to take a query (gmail_waiting builds the
   same query as before) and split `_lookup_names` out of `_resolve_names`.
2. `gmail_search`: `_search_threads` + `_thread_payload` +
   `parsers.gmail_thread_facts`.
3. `slack_search`: page loop over `slack_search_messages`, then
   `_lookup_names` for authors.
4. `docs_search`: `search_drive_files` paged by `nextPageToken`;
   `parsers.drive_files` reads the text lines.
5. `workboard_objectives`: one call; `parsers.workboard_objectives` unwraps.
6. `calendar_day`: `time_min` moves back by `lookback_days`; `_past_kind`
   classifies events before the report day only inside the lookback.
7. `jira_issues`: `_jira_probe` before the queries.
8. Tests in `tests/test_sweep.py`; README tables; version 3.7.0.

## Backend shapes relied on

- workspace-mcp v1.21.2 `search_drive_files(detailed=True)`: text lines
  `- Name: "…" (ID: …, Type: …, …, Modified: …, Last Edited By: …) Link: …`
  and a trailing `nextPageToken: …`.
- mcp-workboard `workboard_get_my_objectives_tool`: `{"objectives": [{objective_id,
  name, progress, owner, team, status_color, dates, key_results: [{key_result_id,
  name, progress, target_date, last_updated}]}], "skipped": [...]}`, or
  `{"error": …}`.
- `slack_search_messages`: the `matches` / `pagination.page_count` shape
  `slack_waiting` already reads, plus `username` and `text` per match.
