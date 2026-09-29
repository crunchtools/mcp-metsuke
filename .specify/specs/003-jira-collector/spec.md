# Spec 003: Jira Issues Collector

> **Status:** Accepted
> **Created:** 2026-09-29
> **Builds on:** the sweep architecture (`sweep/collectors.py`)

## Overview

Release 2.2.0 adds a fifth sweep collector, `jira_issues`. It runs a small set
of named JQL queries through the Trentina gateway and turns each matching issue
into a fixed-shape record, so a report's gatherer can render ticket intake
without ever calling a Jira tool itself.

The driving case is an intake queue: a public request page posts web-form
submissions into a project as issues, and the submitter's answers land as flat
`Field: value` lines in the description. Those requests arrive at all hours and
sit unread. A report that sweeps them surfaces each new one the next morning,
alongside the ones that have been sitting untouched.

Nothing about the collector is project-specific. A definition supplies the
queries; the collector supplies the running, the windowing, the caps, and the
parsing.

## Queries

`options.queries` is an ordered list of 1-6 entries:

- `label` — `[a-z0-9_-]`, ≤32 chars. Names the query on every record it
  produces, so one section can hold several sub-lists ("new", "open").
- `jql` — ≤600 chars. The token `{since}` is replaced with the sweep window's
  start, rendered as a JQL datetime (`YYYY-MM-DD HH:mm`) in the window's own
  zone. `created >= "{since}"` therefore reads "since the last report". A query
  with no placeholder is sent unchanged, which is how a query reaches further
  back than the window ("still open, up to 30 days").
- `limit` — 1-50. `jira_search` refuses anything larger.

Queries run in order, one gateway call each. A query that fails is recorded in
the section's `errors` (the section becomes `partial`) and the remaining queries
still run: one bad JQL string must not cost the report its other lists.

All queries share one cap of 100 records. The cap is checked before each
query's records are appended, so an over-broad first query cannot starve the
rest into silence — it fills the budget and the rest are truncated to what
remains.

## Records

```
{query, key, url, issue_type, status, components, created, age_days,
 summary, contact: {name, email, company}, detail, flagged}
```

- `url` is `browse_url` + key, so a reader clicks through to the ticket.
- `age_days` is computed in code; a gatherer never does date arithmetic.
- `contact` and `detail` come from the description's form fields, when the
  description is form-shaped. `detail` is the non-contact fields joined into
  one line and capped at `detail_chars` (default 800, 0 disables).
- A hand-filed ticket has no `Field: value` pairs. It yields an empty form, so
  `contact` is all-None and `detail` is None, and the record still renders from
  its summary and status.

## Withholding

An intake form carries a stranger's name, address, company, and free text
straight into the description — the classic injection surface. When Trentina
flags a result, the collector keeps only the fields it generated or that come
from a controlled vocabulary (`key`, `url`, `issue_type`, `status`,
`components`, `created`, `age_days`) and withholds `summary`, `detail` and
`contact`, with `flagged: true` on the record. This is the same rule
`feed_entries` applies to a flagged entry's title: metadata survives, submitter
text does not.

## Form parsing

`parsers.jira_form_fields` reads flat `Field: value` lines, keyed by lowercased
field name, ignoring the dashed section headers the forms emit. The first
occurrence of a name wins, so a trailing free-text block that happens to repeat
a field name ("Version: whichever is newest") cannot overwrite the structured
answer above it.

`parsers.jira_search_issues` pulls the `issues` list out of the response,
tolerating a `{"result": ...}` wrapper, and drops non-dict entries.

## Gateway grant

The sweep profile needs the Jira backend with `tools_allow: [jira_search]` —
read-only. No create, comment, or transition tool is reachable from a sweep.

## Out of scope

- Writing to Jira. The sweep reads.
- Any downstream tracking system (spreadsheet, CRM). A separate skill owns
  triage; the report only reports.
- Per-query windows. One window serves the whole sweep; a query that wants a
  different reach says so in its own JQL.
