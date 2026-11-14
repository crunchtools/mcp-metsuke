"""Collectors: the named scripts a sweep step runs.

Each collector makes a fixed, bounded sequence of gateway calls and returns a
section: ``{collector, status, errors, stats, records}``. A failed call is
recorded in ``errors`` and the collector carries on; nothing here raises for a
source failure, so one bad backend can never take out the rest of a sweep.

A result Trentina flagged is kept as metadata only (who, when, where) with its
text withheld, so flagged content never reaches the gathering LLM verbatim.

Calls are sequential by design, never concurrent. Every result is judged by
Trentina's L2/L3 layers on the way through, and the whole point of the sweep is
predictable load on that pipeline: the gathers this replaced failed because
bursts of large or parallel calls were refused. Batch reads are avoided for
the same reason: one judged payload per thread keeps each result under the
admission cap. A sweep is bounded by the per-collector caps below and by
``METSUKE_SWEEP_TIMEOUT_SECONDS``, and it runs off the request path, so its
latency costs nothing interactive.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator

from . import ops_parsers, parsers
from .client import GatewayResult
from .window import SATURDAY, Window, next_weekday

if TYPE_CHECKING:
    from .client import Gateway

MAX_PAGES = 5
MAX_BACKEND_NAME = 64
MAX_ACCOUNT_LENGTH = 200
MAX_TZ_LENGTH = 64

# Slack
SLACK_SEARCH_PAGE_SIZE = 20
SLACK_THREAD_LIMIT = 100
SLACK_THREAD_MAX_PAGES = 3
SLACK_DM_HISTORY_LIMIT = 30
# Channel messages run larger than DM ones: smaller pages stay under the admission cap.
SLACK_CHANNEL_HISTORY_LIMIT = 15
SLACK_ASK_CHARS = 700
SLACK_TAIL_CHARS = 300
DEFAULT_LOOKBACK_DAYS = 7
MAX_LOOKBACK_DAYS = 30
DEFAULT_MAX_CONVERSATIONS = 40
MAX_CONVERSATIONS = 100
MAX_NAME_LOOKUPS = 50
UNKNOWN_PERSON = "unknown person"
DEFAULT_SLACK_SEARCH_MESSAGES = 60
MAX_SLACK_TEXT_CHARS = 2000

# Gmail
GMAIL_SEARCH_PAGE_SIZE = 25
DEFAULT_MAX_THREADS = 60
MAX_THREADS = 200
DEFAULT_BODY_CHARS = 1200
MAX_BODY_CHARS = 4000
MAX_QUERY_EXTRA = 500
MAX_MIN_AGE_HOURS = 168
MAX_PRIORITY_SENDERS = 20
MAX_PRIORITY_SENDER_LENGTH = 100

# Calendar
DEFAULT_LOOKAHEAD_DAYS = 3
MAX_LOOKAHEAD_DAYS = 14
CALENDAR_MAX_RESULTS = 100
CALENDAR_RESPONSES = ("accepted", "tentative", "needsAction")
CalendarResponse = Literal["accepted", "tentative", "needsAction"]
# Free text in a parsed event, all withheld when Trentina flags the result.
FREE_TEXT_EVENT_FIELDS = (
    "title",
    "description",
    "location",
    "organizer",
    "meeting_link",
    "attendees",
)

# Jira
MAX_JIRA_QUERIES = 6
MAX_JIRA_RECORDS = 100
MAX_JQL_CHARS = 600
# jira_search refuses a limit above 50.
JIRA_SEARCH_LIMIT = 50
DEFAULT_JIRA_LIMIT = 25
DEFAULT_JIRA_DETAIL_CHARS = 800
MAX_JIRA_DETAIL_CHARS = 2000
JIRA_FIELDS = "summary,status,issuetype,created,updated,components,labels,description"
JIRA_FIELDS_NO_DESCRIPTION = JIRA_FIELDS.removesuffix(",description")
JIRA_CONTACT_FIELDS = ("name", "email", "company")
# Intake-form fields worth a line in the briefing, in the order they read best.
JIRA_DETAIL_FIELDS = ("image name", "version", "purpose", "page url", "description")

# Drive / Docs
DRIVE_PAGE_SIZE = 25
DEFAULT_MAX_DOCS = 50
MAX_DOCS = 100

# WorkBoard (mcp-workboard accepts at most 25 objective ids)
MAX_WORKBOARD_OBJECTIVES = 25
MAX_WORKBOARD_RECORDS = 150

# Feeds
MAX_FEED_CATEGORIES = 12
MAX_CATEGORY_ID_DIGITS = 9
MAX_FEED_LIMIT = 100
MAX_FEED_RECORDS = 300
MAX_SINCE_DAYS = 30
DEFAULT_SINCE_AFTER_WEEKEND = 3

# Nagios: a week of history, Nagios keeps a month.
DEFAULT_NAGIOS_HOURS = 168
MAX_NAGIOS_HOURS = 744
MAX_NAGIOS_OUTPUT = 300
MAX_NAGIOS_DOWNTIMES = 50

# Ashigaru record issues
MAX_ASHIGARU_RECORDS = 100
MAX_LABEL_LENGTH = 50
# Ashigaru keeps one record issue per label; a few more covers duplicates it closes.
RECORD_ISSUE_PAGE = 10
PUBLISHED_PART = "Published in the last seven days"
VERSION_LABELS = ("org.opencontainers.image.version", "version")


def _section(collector: str) -> dict[str, Any]:
    return {"collector": collector, "status": "ok", "errors": [], "stats": {}, "records": []}


def _note_error(section: dict[str, Any], where: str, result: GatewayResult) -> None:
    section["errors"].append(f"{where}: {result.error}")
    section["status"] = "partial"


def _finish(section: dict[str, Any]) -> dict[str, Any]:
    if section["errors"] and not section["records"] and section["stats"].get("calls_ok", 0) == 0:
        section["status"] = "error"
    return section


def _count_ok(section: dict[str, Any]) -> None:
    section["stats"]["calls_ok"] = section["stats"].get("calls_ok", 0) + 1


def _age_days(then: datetime, now: datetime) -> float:
    return round((now - then).total_seconds() / 86400, 1)


def _bad_json(res: GatewayResult) -> GatewayResult:
    """A parse-failure error that never quotes the response (it may be flagged text)."""
    return GatewayResult(text="", error=f"unparseable result ({len(res.text)} chars)")


# --- Slack ---------------------------------------------------------------


class SlackOptions(BaseModel, extra="forbid"):
    """Options for the slack_waiting collector."""

    backend: str = Field(default="slack", min_length=1, max_length=MAX_BACKEND_NAME)
    user_id: str = Field(..., min_length=2, max_length=32)
    handle: str = Field(..., min_length=1, max_length=64)
    self_label: str = Field(default="you", min_length=1, max_length=64)
    # Group DMs and channels: a question naming the user ("Scott, can you...") is an ask.
    first_name: str | None = Field(default=None, min_length=1, max_length=64)
    lookback_days: int = Field(default=DEFAULT_LOOKBACK_DAYS, ge=1, le=MAX_LOOKBACK_DAYS)
    max_conversations: int = Field(default=DEFAULT_MAX_CONVERSATIONS, ge=1, le=MAX_CONVERSATIONS)
    workspace_url: str = Field(
        default="https://redhat-internal.slack.com", min_length=8, max_length=200
    )


async def slack_waiting(
    gw: Gateway, window: Window, now: datetime, options: dict[str, Any]
) -> dict[str, Any]:
    """Slack DMs and @-mentions the user has not answered.

    Searches DMs (``to:@handle``) and channel mentions (``<@user_id>``) over
    ``lookback_days``, reads each conversation's current state, and keeps only
    ones where the latest direct ask is ``waiting`` (or ``unverified``, when a
    thread was longer than the pages read). Conversations with no direct ask
    count as ``dropped_no_ask``; answered or reacted-to ones as
    ``dropped_answered``. ``in_window``
    marks asks newer than the report window; older ones are the "still open"
    tail. People are resolved to names once per distinct user, after every
    conversation is read, capped at ``MAX_NAME_LOOKUPS`` calls.
    """
    opts = SlackOptions(**options)
    section = _section("slack_waiting")
    conversations = await _search_conversations(gw, opts, now, section)
    section["stats"]["conversations_found"] = len(conversations)
    section["stats"]["dropped_no_ask"] = 0

    ordered = sorted(conversations.values(), key=lambda c: c["latest_ts"], reverse=True)
    dropped_answered = 0
    for conv in ordered[: opts.max_conversations]:
        record, answered = await _read_conversation(gw, opts, conv, window, now, section)
        if answered:
            dropped_answered += 1
        elif record is not None:
            section["records"].append(record)
    section["stats"]["dropped_answered"] = dropped_answered
    section["stats"]["truncated"] = max(0, len(ordered) - opts.max_conversations)

    names = await _resolve_names(gw, opts, section["records"], section)
    for record in section["records"]:
        _apply_names(record, names, opts)
    return _finish(section)


async def _search_conversations(
    gw: Gateway, opts: SlackOptions, now: datetime, section: dict[str, Any]
) -> dict[tuple[str, str], dict[str, Any]]:
    after = (now - timedelta(days=opts.lookback_days + 1)).date().isoformat()
    queries = [f"to:@{opts.handle} after:{after}", f"<@{opts.user_id}> after:{after}"]
    conversations: dict[tuple[str, str], dict[str, Any]] = {}
    for query in queries:
        for page in range(1, MAX_PAGES + 1):
            res = await gw.call(
                opts.backend,
                "slack_search_messages",
                {
                    "query": query,
                    "count": SLACK_SEARCH_PAGE_SIZE,
                    "sort": "timestamp",
                    "sort_dir": "desc",
                    "page": page,
                },
            )
            if not res.ok:
                _note_error(section, f"search {query!r} p{page}", res)
                break
            _count_ok(section)
            try:
                payload = res.json()
            except ValueError:
                _note_error(section, f"search {query!r}", _bad_json(res))
                break
            for match in payload.get("matches") or []:
                _add_match(conversations, match, opts.user_id)
            page_count = _page_count(payload, 1)
            if page >= page_count:
                break
            if page == MAX_PAGES:
                section["stats"]["search_pages_truncated"] = True
    return conversations


def _page_count(payload: Any, default: int) -> int:
    """A Slack search's ``pagination.page_count``, or ``default`` when absent or malformed."""
    pagination = payload.get("pagination") if isinstance(payload, dict) else None
    count = pagination.get("page_count") if isinstance(pagination, dict) else None
    if isinstance(count, bool):
        return default
    try:
        return int(count) if count and int(count) > 0 else default
    except (TypeError, ValueError):
        return default


def _add_match(
    conversations: dict[tuple[str, str], dict[str, Any]], match: dict[str, Any], user_id: str
) -> None:
    """Fold one search hit into its conversation (thread, DM, or single message)."""
    channel = match.get("channel") or {}
    ts = match.get("ts")
    if (
        not channel.get("id")
        or not ts
        or match.get("user") == user_id
        or parsers.slack_is_bot(match)
    ):
        return
    base, thread_ts = parsers.slack_link_parts(match.get("permalink") or "")
    is_dm = bool(channel.get("is_im") or channel.get("is_mpim"))
    # Threads are read as threads. DMs and top-level channel mentions (replies
    # there are later channel messages, not thread replies) are each one
    # conversation per channel, read from history.
    key = (channel["id"], thread_ts or (f"dm:{channel['id']}" if is_dm else f"top:{channel['id']}"))
    conv = conversations.setdefault(key, _new_conversation(channel, is_dm, thread_ts, ts, base))
    conv["earliest_ts"] = min(conv["earliest_ts"], float(ts))
    if float(ts) >= conv["latest_ts"]:
        # Who and when only: search text is not used, since the read decides flagging.
        conv["latest_hit"] = {"user": match.get("user"), "ts": ts}
    conv["latest_ts"] = max(conv["latest_ts"], float(ts))


def _new_conversation(
    channel: dict[str, Any], is_dm: bool, thread_ts: str | None, ts: str, base: str
) -> dict[str, Any]:
    one_to_one = bool(channel.get("is_im"))
    return {
        "channel_id": channel["id"],
        "channel_name": None if one_to_one else channel.get("name"),
        "dm_user": channel.get("user") if one_to_one else None,
        "is_dm": is_dm,
        "thread_ts": thread_ts,
        "earliest_ts": float(ts),
        "latest_ts": float(ts),
        "latest_hit": None,
        "permalink_base": base,
    }


def _conversation_call(conv: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The paged read for a conversation: thread replies, or history from the first hit."""
    if conv["thread_ts"]:
        return "slack_get_thread_replies", {
            "channel_id": conv["channel_id"],
            "thread_ts": conv["thread_ts"],
            "limit": SLACK_THREAD_LIMIT,
        }
    return "slack_get_channel_history", {
        "channel_id": conv["channel_id"],
        "oldest": f"{conv['earliest_ts'] - 1:.6f}",
        "limit": SLACK_DM_HISTORY_LIMIT if conv["is_dm"] else SLACK_CHANNEL_HISTORY_LIMIT,
        "inclusive": True,
    }


async def _fetch_conversation(
    gw: Gateway, opts: SlackOptions, conv: dict[str, Any]
) -> tuple[GatewayResult, list[dict[str, Any]], bool, GatewayResult | None]:
    """A conversation's messages: ``(result, messages, complete, page_failure)``.

    Threads and DMs both follow Slack's cursor for up to
    ``SLACK_THREAD_MAX_PAGES`` pages; ``complete`` is False when more remained
    or a later page could not be read, so a reply beyond what was read is never
    taken as "no reply". The returned result is the last good page's, carrying
    ``flagged=True`` if ANY page was flagged, so text from a flagged page can
    never slip through on an unflagged later one. A failed first page is
    returned as-is for the caller to report; a failed LATER page comes back as
    ``page_failure`` so the caller can record it next to the truncation.
    """
    tool, base_args = _conversation_call(conv)
    messages: list[dict[str, Any]] = []
    cursor: str | None = None
    any_flagged = False
    complete = False
    res = GatewayResult(text="", error="no pages read")
    page_failure: GatewayResult | None = None
    for _ in range(SLACK_THREAD_MAX_PAGES):
        args = {**base_args, "cursor": cursor} if cursor else base_args
        page_res = await gw.call(opts.backend, tool, args)
        any_flagged = any_flagged or page_res.flagged
        page, cursor, readable = _slack_page(page_res)
        if not readable:
            if messages:
                page_failure = page_res if not page_res.ok else _bad_json(page_res)
            else:
                res = page_res
            break
        res = page_res
        messages += page
        if not cursor:  # an empty page with a cursor is not the end
            complete = True
            break
    return replace(res, flagged=any_flagged), messages, complete, page_failure


def _slack_page(res: GatewayResult) -> tuple[list[dict[str, Any]], str | None, bool]:
    """A result's messages, next cursor, and whether it was readable at all."""
    if not res.ok:
        return [], None, False
    try:
        messages, cursor = parsers.slack_page(res.json())
    except ValueError:
        return [], None, False
    return messages, cursor, True


async def _read_conversation(
    gw: Gateway,
    opts: SlackOptions,
    conv: dict[str, Any],
    window: Window,
    now: datetime,
    section: dict[str, Any],
) -> tuple[dict[str, Any] | None, bool]:
    """Fetch one conversation and turn it into a record, or report it answered.

    Records carry user IDs under ``_``-prefixed keys; ``_apply_names`` swaps
    them for names once every conversation has been read.
    """
    res, messages, complete, page_failure = await _fetch_conversation(gw, opts, conv)
    kind = conv["thread_ts"] or ("dm" if conv["is_dm"] else "history")
    where = f"conversation {conv['channel_id']}/{kind}"
    if page_failure is not None:
        _note_error(section, f"{where} later page", page_failure)
    if not res.ok:
        _note_error(section, where, res)
        return None, False
    if not messages:
        _note_error(section, where, _bad_json(res))
        return None, False
    _count_ok(section)

    # Only a one-to-one DM treats any question as an ask; group DMs need targeting.
    one_to_one = conv["dm_user"] is not None
    state = parsers.slack_reply_state(messages, opts.user_id, one_to_one, opts.first_name)
    if state is not None and not conv["is_dm"] and not conv["thread_ts"]:
        # Channel history: the latest messages may be unrelated to the ask.
        state.tail = parsers.slack_tail_from(messages, state.last_ask)
    hit = conv["latest_hit"]
    if state is None and not complete and hit and hit["ts"] not in {m.get("ts") for m in messages}:
        # The search hit that surfaced this conversation lies beyond the pages
        # read: whether it asks anything is unknown, so keep it for the reader.
        state = parsers.ReplyState(last_ask=hit, state="unverified")
    if state is None:
        section["stats"]["dropped_no_ask"] += 1
        return None, False
    if not complete:
        # A reply may sit beyond the pages read: neither "answered" nor
        # "waiting" can be claimed, so the reader decides.
        state.state = "unverified"
    elif state.state == "answered":
        return None, True

    ask = state.last_ask
    ask_ts = str(ask.get("ts"))
    asked_at = datetime.fromtimestamp(float(ask_ts), tz=window.start.tzinfo)
    base = conv["permalink_base"] or opts.workspace_url
    record: dict[str, Any] = {
        "state": state.state,
        "in_window": asked_at >= window.start,
        "age_days": _age_days(asked_at, now),
        "asked_at": asked_at.isoformat(),
        "_asker": ask.get("user"),
        "_conv": conv,
        "permalink": parsers.slack_permalink(base, conv["channel_id"], ask_ts, conv["thread_ts"]),
        "flagged": res.flagged,
        "thread_complete": complete,
        "ask_text": None if res.flagged else (ask.get("text") or "")[:SLACK_ASK_CHARS],
        "tail": []
        if res.flagged
        else [
            {
                "_author": m.get("user"),
                "text": (m.get("text") or "")[:SLACK_TAIL_CHARS],
                "reacted_by_user": parsers.slack_reacted_by(m, opts.user_id),
            }
            for m in state.tail
        ],
    }
    return record, False


def _user_ids(records: list[dict[str, Any]], self_id: str) -> list[str]:
    """Distinct user IDs the records mention, in first-seen order, excluding the user."""
    seen: dict[str, None] = {}
    for record in records:
        ids = [record["_asker"], record["_conv"]["dm_user"]]
        ids += [t["_author"] for t in record["tail"]]
        for uid in ids:
            if uid and uid != self_id and uid not in parsers.SLACK_SYSTEM_USERS:
                seen.setdefault(uid, None)
    return list(seen)


async def _resolve_names(
    gw: Gateway, opts: SlackOptions, records: list[dict[str, Any]], section: dict[str, Any]
) -> dict[str, str]:
    return await _lookup_names(gw, opts.backend, _user_ids(records, opts.user_id), section)


async def _lookup_names(
    gw: Gateway, backend: str, ids: list[str], section: dict[str, Any]
) -> dict[str, str]:
    """Display names for Slack user IDs, one call each, capped at ``MAX_NAME_LOOKUPS``."""
    names: dict[str, str] = {}
    section["stats"]["name_lookups_skipped"] = max(0, len(ids) - MAX_NAME_LOOKUPS)
    for uid in ids[:MAX_NAME_LOOKUPS]:
        res = await gw.call(backend, "slack_get_user_info", {"user_id": uid})
        if not res.ok:
            _note_error(section, f"user {uid}", res)
            continue
        try:
            name = parsers.slack_real_name(res.json())
        except ValueError:
            _note_error(section, f"user {uid}", _bad_json(res))
            continue
        if name:
            names[uid] = name
    return names


def _apply_names(record: dict[str, Any], names: dict[str, str], opts: SlackOptions) -> None:
    def label(uid: str | None) -> str:
        if uid == opts.user_id:
            return opts.self_label
        return names.get(uid or "", UNKNOWN_PERSON)

    conv = record.pop("_conv")
    record["asker"] = label(record.pop("_asker"))
    if conv["dm_user"]:
        record["where"] = f"DM with {label(conv['dm_user'])}"
    elif conv["is_dm"]:
        record["where"] = "group DM"
    else:
        record["where"] = f"#{conv['channel_name']}" if conv["channel_name"] else conv["channel_id"]
    for item in record["tail"]:
        item["author"] = label(item.pop("_author"))


# --- Gmail ---------------------------------------------------------------


class GmailOptions(BaseModel, extra="forbid"):
    """Options for the gmail_waiting collector."""

    backend: str = Field(..., min_length=1, max_length=MAX_BACKEND_NAME)
    account: str = Field(..., min_length=3, max_length=MAX_ACCOUNT_LENGTH)
    query_extra: str = Field(default="", max_length=MAX_QUERY_EXTRA)
    max_threads: int = Field(default=DEFAULT_MAX_THREADS, ge=1, le=MAX_THREADS)
    body_chars: int = Field(default=DEFAULT_BODY_CHARS, ge=0, le=MAX_BODY_CHARS)
    link_template: str | None = Field(
        default="https://mail.google.com/mail/u/0/#all/{thread_id}", max_length=200
    )
    # Search this many days back instead of the report window.
    lookback_days: int | None = Field(default=None, ge=1, le=MAX_LOOKBACK_DAYS)
    # Drop threads whose last message is newer than this: the user handles fresh mail.
    min_age_hours: int = Field(default=0, ge=0, le=MAX_MIN_AGE_HOURS)
    # Case-insensitive substrings of the From header that mark a record priority.
    priority_senders: list[str] = Field(default_factory=list, max_length=MAX_PRIORITY_SENDERS)

    @field_validator("priority_senders")
    @classmethod
    def _check_priority_senders(cls, value: list[str]) -> list[str]:
        for sender in value:
            if not sender.strip() or len(sender) > MAX_PRIORITY_SENDER_LENGTH:
                raise ValueError(
                    f"priority_senders entries must be 1-{MAX_PRIORITY_SENDER_LENGTH} characters"
                )
        return value


async def gmail_waiting(
    gw: Gateway, window: Window, now: datetime, options: dict[str, Any]
) -> dict[str, Any]:
    """Inbox threads in the window where the ball is in the user's court.

    Uses the backend's own ownership analysis (``include_analysis``) for the
    "who sent last / who owes a reply" decision, drops automated mail and bare
    calendar notices, and keeps invitations (they can carry a prep ask).

    When the backend returns no analysis for a thread, it is kept with
    ``ownership_known: false`` rather than dropped: missing a thread the user
    owes is worse than showing one the reader can dismiss. ``stats`` counts
    these as ``ownership_unknown``.

    ``lookback_days`` replaces the report window with the last N days, and
    ``min_age_hours`` drops threads active more recently than that
    (``dropped_fresh``), so the section lists mail that has been waiting.
    Senders matching ``priority_senders`` get ``priority: true``.
    """
    opts = GmailOptions(**options)
    section = _section("gmail_waiting")
    if opts.lookback_days is not None:
        window = Window(start=now - timedelta(days=opts.lookback_days), end=window.end)
    query = f"in:inbox after:{window.start:%Y/%m/%d} {opts.query_extra}".strip()
    thread_ids = await _search_threads(
        gw, opts.backend, opts.account, query, opts.max_threads, section
    )

    stats = section["stats"]
    stats.update(
        threads_found=len(thread_ids),
        dropped_self=0,
        dropped_noise=0,
        dropped_not_owed=0,
        dropped_old=0,
        dropped_fresh=0,
        ownership_unknown=0,
    )
    stats["truncated"] = max(0, len(thread_ids) - opts.max_threads)
    for tid in thread_ids[: opts.max_threads]:
        res = await gw.call(
            opts.backend,
            "get_gmail_thread_content",
            {"thread_id": tid, "user_google_email": opts.account, "include_analysis": True},
        )
        if not res.ok:
            _note_error(section, f"thread {tid}", res)
            continue
        _count_ok(section)
        record = _gmail_record(res, tid, opts, window, now, stats)
        if record is not None:
            section["records"].append(record)
    return _finish(section)


async def _search_threads(
    gw: Gateway,
    backend: str,
    account: str,
    query: str,
    max_threads: int,
    section: dict[str, Any],
) -> list[str]:
    """Thread IDs matching a Gmail query, paged until ``max_threads`` or ``MAX_PAGES``."""
    thread_ids: list[str] = []
    token: str | None = None
    for page in range(1, MAX_PAGES + 1):
        args: dict[str, Any] = {
            "query": query,
            "user_google_email": account,
            "page_size": GMAIL_SEARCH_PAGE_SIZE,
        }
        if token:
            args["page_token"] = token
        res = await gw.call(backend, "search_gmail_messages", args)
        if not res.ok:
            _note_error(section, f"search p{page}", res)
            break
        _count_ok(section)
        page_ids, token = parsers.gmail_search_page(res.text)
        thread_ids += [t for t in page_ids if t not in thread_ids]
        if not token:
            break
        if len(thread_ids) >= max_threads or page == MAX_PAGES:
            # More results exist than will be read: say so.
            section["stats"]["search_pages_truncated"] = True
            break
    return thread_ids


def _thread_payload(res: GatewayResult) -> tuple[str, dict[str, Any]]:
    """The thread text and ownership analysis; plain text means no analysis."""
    try:
        payload = res.json()
    except ValueError:
        return res.text, {}
    if not isinstance(payload, dict):
        return res.text, {}
    return str(payload.get("content") or ""), dict(payload.get("analysis") or {})


def _drop_reason(
    facts: dict[str, Any], window: Window, account: str, fresh_after: datetime | None = None
) -> str | None:
    """The stats key a thread is dropped under, or None to keep it.

    Args:
        facts: ``parsers.gmail_thread_facts`` output for the thread.
        window: The report window; threads last active before it are old.
        account: The mailbox address; a thread it sent last is the user's own.
        fresh_after: Threads last active after this are too new to list.
    """
    if (parsers.email_address(facts["sender"]) or "").lower() == account.lower():
        # The user sent the last message (their own reports, sent replies):
        # nothing is waiting on them, whatever the ownership analysis says.
        return "dropped_self"
    if parsers.gmail_is_noise(facts["subject"], facts["sender"]):
        return "dropped_noise"
    if facts["ball"] not in (None, "user"):
        return "dropped_not_owed"
    if facts["last_at"] is not None and facts["last_at"] < window.start:
        return "dropped_old"
    if fresh_after is not None and facts["last_at"] is not None and facts["last_at"] > fresh_after:
        return "dropped_fresh"
    return None


def _gmail_record(
    res: GatewayResult,
    tid: str,
    opts: GmailOptions,
    window: Window,
    now: datetime,
    stats: dict[str, Any],
) -> dict[str, Any] | None:
    facts = parsers.gmail_thread_facts(*_thread_payload(res))
    fresh_after = now - timedelta(hours=opts.min_age_hours) if opts.min_age_hours else None
    reason = _drop_reason(facts, window, opts.account, fresh_after)
    if reason is not None:
        stats[reason] += 1
        return None
    stats["ownership_unknown"] += facts["ball"] is None
    last_at: datetime | None = facts["last_at"]
    subject: str | None = facts["subject"]
    sender = (facts["sender"] or "").lower()
    return {
        "thread_id": tid,
        "link": opts.link_template.format(thread_id=tid) if opts.link_template else None,
        "subject": None if res.flagged else subject,
        "from": parsers.email_address(facts["sender"]) if res.flagged else facts["sender"],
        "last_at": last_at.isoformat() if last_at else None,
        "age_days": _age_days(last_at, now) if last_at else None,
        "message_count": facts["message_count"],
        "participant_count": facts["participant_count"],
        "is_invitation": bool(subject and subject.lower().startswith("invitation:")),
        "ownership_known": facts["ball"] is not None,
        "priority": any(p.lower() in sender for p in opts.priority_senders),
        "flagged": res.flagged,
        "body": None if res.flagged or not opts.body_chars else facts["body"][: opts.body_chars],
    }


# --- Calendar ------------------------------------------------------------


def valid_zone(value: str) -> str:
    """Return ``value`` if it names an IANA timezone; raise ValueError otherwise.

    Shared by the sweep spec and the calendar options so a bad zone fails at
    ``upsert_definition`` time, not when the scheduler fires.
    """
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"not an IANA timezone: {value!r}") from exc
    return value


class CalendarOptions(BaseModel, extra="forbid"):
    """Options for the calendar_day collector."""

    backend: str = Field(..., min_length=1, max_length=MAX_BACKEND_NAME)
    account: str = Field(..., min_length=3, max_length=MAX_ACCOUNT_LENGTH)
    timezone: str = Field(default="America/New_York", min_length=1, max_length=MAX_TZ_LENGTH)
    lookahead_days: int = Field(default=DEFAULT_LOOKAHEAD_DAYS, ge=0, le=MAX_LOOKAHEAD_DAYS)
    # Also read this many days before the report day; those events are past_*.
    lookback_days: int = Field(default=0, ge=0, le=MAX_LOOKBACK_DAYS)
    # Keep only events the user answered this way; None keeps every non-declined one.
    responses: list[CalendarResponse] | None = Field(
        default=None, min_length=1, max_length=len(CALENDAR_RESPONSES)
    )

    _check_timezone = field_validator("timezone")(valid_zone)


def _past_kind(event: dict[str, Any], start_day: date, first_day: date | None) -> str | None:
    """past_meeting / past_all_day for an answered event inside the lookback, else None."""
    if first_day is None or start_day < first_day or event["my_status"] == "needsAction":
        return None
    return "past_all_day" if event["all_day"] else "past_meeting"


def _event_kind(event: dict[str, Any], day0: datetime, first_day: date | None = None) -> str | None:
    """meeting / pending_invite / all_day / past_meeting / past_all_day, or None to drop.

    Declined events are always dropped. Events starting before the report day
    are ``past_*`` only when they start on or after ``first_day`` (the
    lookback's first day); without a lookback they are dropped, as are
    events that merely overlap the start of the read.
    """
    start = parsers.event_start(event)
    status = event["my_status"]
    if status == "declined" or start is None:
        return None
    start_day = _local_day(start, day0)
    if start_day < day0.date():
        return _past_kind(event, start_day, first_day)
    on_day0 = start_day == day0.date()
    if on_day0 and event["all_day"]:
        return "all_day"
    if on_day0 and status != "needsAction":
        return "meeting"
    if status == "needsAction" and start_day >= day0.date():
        return "pending_invite"
    return None


def _local_day(start: datetime, day0: datetime) -> date:
    """The report-timezone date of an event start; all-day starts are already dates."""
    return start.astimezone(day0.tzinfo).date() if start.tzinfo else start.date()


async def calendar_day(
    gw: Gateway,
    _window: Window,
    now: datetime,
    options: dict[str, Any],
) -> dict[str, Any]:
    """The report day's meetings, pending invites, and hard overlaps.

    The report day is today, or the next weekday on a weekend run. Declined
    events are dropped. Records carry ``kind``: ``meeting`` (report day,
    accepted/tentative/organizer), ``pending_invite`` (needsAction within the
    lookahead), or ``all_day``.

    ``lookback_days`` widens the read to that many days before the report day;
    those events come back as ``past_meeting`` / ``past_all_day`` (needsAction
    ones are dropped: an unanswered invite in the past is not a meeting held).
    ``responses`` keeps only events the user answered that way, e.g.
    ``["accepted", "tentative"]``. Both default to the daily-briefing shape.
    """
    opts = CalendarOptions(**options)
    section = _section("calendar_day")
    day0 = next_weekday(now, opts.timezone)
    horizon = day0 + timedelta(days=opts.lookahead_days + 1)
    time_min = day0 - timedelta(days=opts.lookback_days)
    res = await gw.call(
        opts.backend,
        "get_events",
        {
            "user_google_email": opts.account,
            "time_min": time_min.isoformat(),
            "time_max": horizon.isoformat(),
            "detailed": True,
            "max_results": CALENDAR_MAX_RESULTS,
        },
    )
    if not res.ok:
        _note_error(section, "get_events", res)
        return _finish(section)
    _count_ok(section)
    section["stats"]["report_day"] = day0.date().isoformat()
    same_day: list[dict[str, Any]] = []
    if opts.lookback_days:
        section["stats"]["lookback_from"] = time_min.date().isoformat()
    for event in parsers.calendar_events(res.text, opts.account):
        kind = _event_kind(event, day0, time_min.date() if opts.lookback_days else None)
        unwanted = opts.responses is not None and event["my_status"] not in opts.responses
        if kind is None or unwanted:
            continue
        record = {"kind": kind, **event, "flagged": res.flagged}
        if res.flagged:
            for text_field in FREE_TEXT_EVENT_FIELDS:
                record[text_field] = None
        section["records"].append(record)
        start = parsers.event_start(event)
        if kind != "all_day" and start is not None and _local_day(start, day0) == day0.date():
            same_day.append(record)
    _mark_overlaps(same_day)
    return _finish(section)


def _mark_overlaps(events: list[dict[str, Any]]) -> None:
    """Record each hard overlap by the other event's start time (never its title)."""
    timed = [(parsers.event_start(e), parsers.event_end(e), e) for e in events if not e["all_day"]]
    for i, (s1, e1, ev1) in enumerate(timed):
        for s2, e2, ev2 in timed[i + 1 :]:
            if s1 and e1 and s2 and e2 and s1 < e2 and s2 < e1:
                ev1.setdefault("overlaps", []).append(ev2["start"])
                ev2.setdefault("overlaps", []).append(ev1["start"])


# --- Feeds ---------------------------------------------------------------


class FeedOptions(BaseModel, extra="forbid"):
    """Options for the feed_entries collector."""

    backend: str = Field(default="feeds", min_length=1, max_length=MAX_BACKEND_NAME)
    categories: dict[str, int] = Field(..., min_length=1, max_length=MAX_FEED_CATEGORIES)
    since_days: int = Field(default=1, ge=1, le=MAX_SINCE_DAYS)
    since_days_after_weekend: int = Field(
        default=DEFAULT_SINCE_AFTER_WEEKEND, ge=1, le=MAX_SINCE_DAYS
    )

    @field_validator("categories")
    @classmethod
    def _check_categories(cls, value: dict[str, int]) -> dict[str, int]:
        for category, limit in value.items():
            ascii_digits = category.isascii() and category.isdecimal()
            if not ascii_digits or len(category) > MAX_CATEGORY_ID_DIGITS:
                raise ValueError(
                    f"feed category ids are numeric strings of at most "
                    f"{MAX_CATEGORY_ID_DIGITS} digits, got {category[:20]!r}"
                )
            if not 1 <= limit <= MAX_FEED_LIMIT:
                raise ValueError(f"category {category} limit must be 1-{MAX_FEED_LIMIT}")
        return value


async def feed_entries(
    gw: Gateway, window: Window, now: datetime, options: dict[str, Any]
) -> dict[str, Any]:
    """Recent feed entries per category, read or unread (read-state is not importance).

    The lookback starts at the sweep window's start or ``since_days`` ago,
    whichever is earlier, so re-runs later in the day cover the same span.
    After a weekend (Monday, or a weekend run) ``since_days_after_weekend``
    applies instead. An empty category comes back as empty text and counts as
    zero entries. Output is capped at ``MAX_FEED_RECORDS``.
    """
    opts = FeedOptions(**options)
    section = _section("feed_entries")
    local = now.astimezone(window.start.tzinfo)
    weekend_gap = local.weekday() == 0 or local.weekday() >= SATURDAY
    since = opts.since_days_after_weekend if weekend_gap else opts.since_days
    section["stats"]["since_days"] = since
    after = min(window.start, now - timedelta(days=since)).astimezone(UTC)
    published_after = after.strftime("%Y-%m-%dT%H:%M:%SZ")
    section["stats"]["published_after"] = published_after
    for category, limit in opts.categories.items():
        res = await gw.call(
            opts.backend,
            "list_entries_tool",
            {
                "category_id": int(category),
                "published_after": published_after,
                "unread_only": False,
                "limit": limit,
            },
        )
        if not res.ok:
            _note_error(section, f"category {category}", res)
            continue
        _count_ok(section)
        try:
            entries = res.json() if res.text.strip() else []
        except ValueError:
            _note_error(section, f"category {category}", _bad_json(res))
            continue
        if isinstance(entries, dict):
            entries = entries.get("result", [])
        room = MAX_FEED_RECORDS - len(section["records"])
        for entry in (entries if isinstance(entries, list) else [])[: max(0, room)]:
            section["records"].append(
                {
                    "entry_id": entry.get("id"),
                    "category_id": int(category),
                    "title": None if res.flagged else entry.get("title"),
                    "url": entry.get("url"),
                    "feed": None if res.flagged else entry.get("feed_title"),
                    "published": entry.get("published"),
                    "flagged": res.flagged,
                }
            )
    return _finish(section)


# --- Jira ----------------------------------------------------------------


class JiraQuery(BaseModel, extra="forbid"):
    """One named JQL query the jira_issues collector runs."""

    label: str = Field(..., min_length=1, max_length=32, pattern=r"^[a-z0-9_-]+$")
    jql: str = Field(..., min_length=1, max_length=MAX_JQL_CHARS)
    limit: int = Field(default=DEFAULT_JIRA_LIMIT, ge=1, le=JIRA_SEARCH_LIMIT)
    # False skips the intake-form description: no contact or detail, and none
    # of the customer's free text that can get a whole result flagged.
    description: bool = True


class JiraOptions(BaseModel, extra="forbid"):
    """Options for the jira_issues collector."""

    queries: list[JiraQuery] = Field(..., min_length=1, max_length=MAX_JIRA_QUERIES)
    backend: str = Field(default="jira", min_length=1, max_length=MAX_BACKEND_NAME)
    browse_url: str = Field(default="https://redhat.atlassian.net/browse/", max_length=200)
    detail_chars: int = Field(default=DEFAULT_JIRA_DETAIL_CHARS, ge=0, le=MAX_JIRA_DETAIL_CHARS)
    # A key known to exist: if the backend cannot return it, the section is an
    # error instead of "ok, nothing new".
    probe_key: str | None = Field(default=None, max_length=32, pattern=r"^[A-Z][A-Z0-9_]+-\d+$")


async def _jira_probe(gw: Gateway, opts: JiraOptions, key: str, section: dict[str, Any]) -> bool:
    """Whether the backend returns a known issue; a False answer is recorded as an error."""
    res = await gw.call(
        opts.backend, "jira_search", {"jql": f"key = {key}", "limit": 1, "fields": "status"}
    )
    if not res.ok:
        _note_error(section, f"probe {key}", res)
        return False
    try:
        payload = res.json() if res.text.strip() else []
    except ValueError:
        _note_error(section, f"probe {key}", _bad_json(res))
        return False
    if not parsers.listed_dicts(payload, "issues"):
        missing = f"jira backend returned nothing for known key {key}"
        _note_error(section, f"probe {key}", GatewayResult(text="", error=missing))
        return False
    _count_ok(section)
    return True


def _jira_created(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _jira_detail(form: dict[str, str], limit: int) -> str | None:
    """The form's non-contact fields as one capped line, or None when there are none."""
    if not limit:
        return None
    detail = " | ".join(f"{name}: {form[name]}" for name in JIRA_DETAIL_FIELDS if form.get(name))
    return detail[:limit] or None


def _jira_record(
    issue: dict[str, Any], label: str, opts: JiraOptions, now: datetime, flagged: bool
) -> dict[str, Any]:
    """One issue as a record; a flagged result keeps only its non-text fields."""
    created = _jira_created(issue.get("created"))
    form = {} if flagged else parsers.jira_form_fields(issue.get("description"))
    return {
        "query": label,
        "key": issue.get("key"),
        "url": f"{opts.browse_url}{issue.get('key')}",
        "issue_type": (issue.get("issue_type") or {}).get("name"),
        "status": (issue.get("status") or {}).get("name"),
        "components": issue.get("components") or [],
        "labels": issue.get("labels") or [],
        "created": created.isoformat() if created else None,
        "age_days": _age_days(created, now) if created else None,
        "updated": issue.get("updated"),
        "summary": None if flagged else issue.get("summary"),
        "contact": {name: form.get(name) for name in JIRA_CONTACT_FIELDS},
        "detail": _jira_detail(form, opts.detail_chars),
        "flagged": flagged,
    }


async def jira_issues(
    gw: Gateway, window: Window, now: datetime, options: dict[str, Any]
) -> dict[str, Any]:
    """Issues matching each named JQL query, with any intake-form fields parsed.

    ``{since}`` in a query becomes the window start as a JQL datetime in the
    window's zone, so ``created >= "{since}"`` reads "since the last report".
    A query without the placeholder is sent unchanged.

    Queries run in order and share one ``MAX_JIRA_RECORDS`` budget; a query
    that fails is recorded and the remaining ones still run. The intake forms
    on the request page put the reporter's name, address and technical specs
    in the description, so a flagged result withholds ``summary``, ``detail``
    and ``contact`` and keeps only the key, link, status and dates (``created``, ``updated``).

    ``probe_key`` first fetches an issue known to exist. A backend that cannot
    return it (an error, or an empty result such as ``total: -1``) is broken,
    not quiet: the queries are skipped and the section is ``error``, so a
    report never reads "0 new tickets" off a dead connection.
    """
    opts = JiraOptions(**options)
    section = _section("jira_issues")
    since = window.start.strftime("%Y-%m-%d %H:%M")
    section["stats"]["since"] = since
    if opts.probe_key and not await _jira_probe(gw, opts, opts.probe_key, section):
        section["status"] = "error"
        return section
    for query in opts.queries:
        res = await gw.call(
            opts.backend,
            "jira_search",
            {
                "jql": query.jql.replace("{since}", since),
                "limit": query.limit,
                "fields": JIRA_FIELDS if query.description else JIRA_FIELDS_NO_DESCRIPTION,
            },
        )
        if not res.ok:
            _note_error(section, f"query {query.label}", res)
            continue
        _count_ok(section)
        try:
            payload = res.json() if res.text.strip() else []
        except ValueError:
            _note_error(section, f"query {query.label}", _bad_json(res))
            continue
        room = MAX_JIRA_RECORDS - len(section["records"])
        for issue in parsers.listed_dicts(payload, "issues")[: max(0, room)]:
            section["records"].append(_jira_record(issue, query.label, opts, now, res.flagged))
    return _finish(section)


# --- Nagios --------------------------------------------------------------


class NagiosOptions(BaseModel, extra="forbid"):
    """Options for the nagios_health collector."""

    backend: str = Field(default="nagios", min_length=1, max_length=MAX_BACKEND_NAME)
    hours: int = Field(default=DEFAULT_NAGIOS_HOURS, ge=1, le=MAX_NAGIOS_HOURS)
    min_events: int = Field(default=5, ge=1, le=1000)
    base_url: str = Field(default="https://nagios.crunchtools.com/cgi-bin/", max_length=200)
    timezone: str = Field(default="America/New_York", min_length=1, max_length=MAX_TZ_LENGTH)

    _check_timezone = field_validator("timezone")(valid_zone)


def _local(stamp: str | None, zone: ZoneInfo) -> str | None:
    """A ``…Z`` timestamp as reader time, "Oct 24 7:12 PM"; None passes through."""
    if not stamp:
        return None
    moment = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(zone)
    return f"{moment:%b} {moment.day} {moment:%-I:%M %p}"


def _nagios_links(base: str, host: str, service: str | None) -> tuple[str, str]:
    h = quote(host, safe="")
    if service is None:
        return f"{base}extinfo.cgi?type=1&host={h}", f"{base}history.cgi?host={h}"
    s = quote(service, safe="")
    return (
        f"{base}extinfo.cgi?type=2&host={h}&service={s}",
        f"{base}history.cgi?host={h}&service={s}",
    )


async def _nagios_text(
    gw: Gateway, section: dict[str, Any], backend: str, tool: str, args: dict[str, Any]
) -> GatewayResult | None:
    res = await gw.call(backend, tool, args)
    if not res.ok:
        _note_error(section, tool, res)
        return None
    _count_ok(section)
    return res


def _merge_rollup(
    section: dict[str, Any],
    res: GatewayResult,
    checks: dict[tuple[str, str | None], dict[str, Any]],
    check: Callable[[str, str | None], dict[str, Any]],
    min_events: int,
    zone: ZoneInfo,
) -> None:
    """Fold the alert roll-up into ``checks``: its stats, and every row that qualifies."""
    rows = ops_parsers.nagios_alert_rollup(res.text)
    total, more = ops_parsers.nagios_rollup_extent(res.text)
    planned_events, planned_objects = ops_parsers.nagios_planned_events(res.text)
    section["stats"].update(
        checks_with_problems=total,
        rollup_listed=len(rows),
        rollup_more=more,
        planned_events=planned_events,
        planned_objects=planned_objects,
    )
    for row in rows:
        key = (row["host"], row["service"])
        if key not in checks and row["events"] < min_events:
            continue
        check(*key).update(
            events=row["events"],
            counts=row["counts"],
            hard_events=row["hard"],
            first=_local(row["first"], zone),
            last=_local(row["last"], zone),
            last_state=row["last_state"],
            last_output=None if res.flagged else row["output"][:MAX_NAGIOS_OUTPUT],
        )


def _nagios_downtimes(res: GatewayResult, zone: ZoneInfo) -> list[dict[str, Any]]:
    """Downtime windows in reader time; the comment is withheld when flagged."""
    return [
        {
            **row,
            "start": _local(row["start"], zone),
            "end": _local(row["end"], zone),
            "comment": None if res.flagged else row["comment"][:MAX_NAGIOS_OUTPUT],
        }
        for row in ops_parsers.nagios_downtimes(res.text)[:MAX_NAGIOS_DOWNTIMES]
    ]


async def nagios_health(
    gw: Gateway, _window: Window, _now: datetime, options: dict[str, Any]
) -> dict[str, Any]:
    """What is broken now and what kept breaking, one record per qualifying check.

    A check qualifies when it is not OK now, had ``min_events`` or more problem
    events in ``hours``, or paged. Only hard CRITICAL and DOWN page Hermes, and
    the roll-up does not split its hard count by state, so the notification
    history is what says a check went hard CRITICAL. The roll-up lists its
    noisiest rows and counts the rest. Records come sorted open
    first, then noisiest, and carry their status and history links already
    URL-encoded and their times in ``timezone``. Problem events during
    scheduled downtime are left out of the roll-up and counted in
    ``planned_events``/``planned_objects``; the downtime windows themselves
    go in ``stats.downtimes``. Plugin output and downtime comments are
    withheld from any result Trentina flagged; counts, states and times are kept.
    """
    opts = NagiosOptions(**options)
    zone = ZoneInfo(opts.timezone)
    section = _section("nagios_health")
    checks: dict[tuple[str, str | None], dict[str, Any]] = {}

    def check(host: str, service: str | None) -> dict[str, Any]:
        key = (host, service)
        if key not in checks:
            status_url, history_url = _nagios_links(opts.base_url, host, service)
            checks[key] = {
                "host": host,
                "service": service,
                "open_now": False,
                "in_downtime": False,
                "pages": 0,
                "events": 0,
                "status_url": status_url,
                "history_url": history_url,
            }
        return checks[key]

    if res := await _nagios_text(gw, section, opts.backend, "nagios_current_problems_tool", {}):
        for row in ops_parsers.nagios_current_problems(res.text):
            record = check(row["host"], row["service"])
            record.update(
                open_now=True,
                state_now=row["state"],
                since=_local(row["since"], zone),
                duration=row["duration"],
                hard_now=row["hard"],
                acknowledged=row["acknowledged"],
                in_downtime=row["in_downtime"],
                output_now=None if res.flagged else row["output"][:MAX_NAGIOS_OUTPUT],
            )
    args = {"hours": opts.hours}
    if res := await _nagios_text(
        gw, section, opts.backend, "nagios_notification_history_tool", args
    ):
        pages = ops_parsers.nagios_notifications(res.text)
        section["stats"]["pages"] = len(pages)
        for row in pages:
            record = check(row["host"], row["service"])
            record["pages"] += 1
            record["last_page"] = _local(row["at"], zone)
    if res := await _nagios_text(gw, section, opts.backend, "nagios_alert_history_tool", args):
        _merge_rollup(section, res, checks, check, opts.min_events, zone)
    if res := await _nagios_text(gw, section, opts.backend, "nagios_downtime_history_tool", args):
        section["stats"]["downtimes"] = _nagios_downtimes(res, zone)
    section["records"] = sorted(
        checks.values(),
        key=lambda r: (not r["open_now"], -r["events"], -r["pages"], r["host"], r["service"] or ""),
    )
    section["stats"]["qualifying"] = len(section["records"])
    return _finish(section)


# --- Ashigaru record issues ----------------------------------------------


class AshigaruOptions(BaseModel, extra="forbid"):
    """Options for the ashigaru_issue collector."""

    backend: str = Field(default="github", min_length=1, max_length=MAX_BACKEND_NAME)
    owner: str = Field(default="crunchtools", min_length=1, max_length=100)
    repo: str = Field(default="ashigaru", min_length=1, max_length=100)
    label: str = Field(..., min_length=1, max_length=MAX_LABEL_LENGTH, pattern=r"^[a-z0-9-]+$")


async def _record_issue(
    gw: Gateway, section: dict[str, Any], opts: AshigaruOptions
) -> tuple[dict[str, Any], bool] | None:
    """The oldest open issue carrying the label (Ashigaru edits that one), and its flag."""
    res = await gw.call(
        opts.backend,
        "list_issues_tool",
        {
            "owner": opts.owner,
            "repo": opts.repo,
            "labels": opts.label,
            "state": "open",
            "per_page": RECORD_ISSUE_PAGE,
        },
    )
    if not res.ok:
        _note_error(section, f"label {opts.label}", res)
        return None
    _count_ok(section)
    try:
        payload = res.json()
    except ValueError:
        _note_error(section, f"label {opts.label}", _bad_json(res))
        return None
    items = payload.get("items", []) if isinstance(payload, dict) else payload
    issues = sorted(
        (i for i in items or [] if isinstance(i, dict) and isinstance(i.get("number"), int)),
        key=lambda i: i["number"],
    )
    if not issues:
        _note_error(section, f"label {opts.label}", GatewayResult(text="", error="no open issue"))
        return None
    section["stats"].update(
        issue_number=issues[0]["number"],
        issue_url=issues[0].get("html_url"),
        issue_updated=issues[0].get("updated_at"),
    )
    return issues[0], res.flagged


def _capped_parts(parts: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Each part's items capped at ``MAX_ASHIGARU_RECORDS``, and its stats written to match:
    ``listed`` is what is emitted, ``more`` adds what the cap dropped to the issue's own tail."""
    for part in parts.values():
        dropped = max(0, len(part["items"]) - MAX_ASHIGARU_RECORDS)
        part["items"] = part["items"][:MAX_ASHIGARU_RECORDS]
        part["more"] += dropped
    return {
        name: {"count": part["count"], "listed": len(part["items"]), "more": part["more"]}
        for name, part in parts.items()
    }


async def ashigaru_issue(
    gw: Gateway, _window: Window, _now: datetime, options: dict[str, Any]
) -> dict[str, Any]:
    """Every item of an Ashigaru record issue (inbox or releases), one record each.

    The issue is found by label, not number, so a renumbered record is still
    read. Each record names its ``part`` heading; ``stats.parts`` gives every
    part's own total and how many it lists, so "and 17 more" reads as a cap,
    not a gap. Titles are written by contributors, so a flagged result keeps
    every field but ``title``.
    """
    opts = AshigaruOptions(**options)
    section = _section("ashigaru_issue")
    found = await _record_issue(gw, section, opts)
    if found is None:
        return _finish(section)
    issue, flagged = found
    section["stats"]["flagged"] = flagged
    parts = ops_parsers.ashigaru_parts(issue.get("body") or "")
    section["stats"]["parts"] = _capped_parts(parts)
    for name, part in parts.items():
        for item in part["items"]:
            section["records"].append(
                {"part": name, **item, "title": None if flagged else item["title"]}
            )
    return _finish(section)


class ReleasesOptions(AshigaruOptions, extra="forbid"):
    """Options for the crunchtools_releases collector."""

    label: str = Field(
        default="ashigaru-releases",
        min_length=1,
        max_length=MAX_LABEL_LENGTH,
        pattern=r"^[a-z0-9-]+$",
    )
    podman_backend: str = Field(default="podman", min_length=1, max_length=MAX_BACKEND_NAME)


def _running_versions(payload: Any) -> dict[str, set[str]]:
    """Repo name to the versions its running containers report.

    A container belongs to a repository when its OCI source label ends in the
    repo name, or failing that when its image name is the repo name.
    """
    items = payload.get("items", []) if isinstance(payload, dict) else payload
    running: dict[str, set[str]] = {}
    for ctr in items or []:
        if not isinstance(ctr, dict) or ctr.get("State") not in (None, "running"):
            continue
        labels = ctr.get("Labels") or {}
        source = str(labels.get("org.opencontainers.image.source") or "").rstrip("/")
        image = str(ctr.get("Image") or "").rsplit("/", 1)[-1].split(":", 1)[0]
        repo = source.rsplit("/", 1)[-1] if source else image
        # The OCI label reads "main" on images built from a branch; the
        # project's own "version" label carries the release then.
        version = next(
            (
                str(v)
                for v in (labels.get(k) for k in VERSION_LABELS)
                if ops_parsers.semver(str(v or ""))
            ),
            None,
        )
        if repo and version:
            running.setdefault(repo, set()).add(version.lstrip("v"))
    return running


def _deploy_status(newest: str, running: set[str]) -> str:
    """live, behind, or not-deployed. Running a version newer than the newest
    release (release mode off) counts as live."""
    if not running:
        return "not-deployed"
    best = max(running, key=lambda v: ops_parsers.semver(v) or (0, 0, 0, 0))
    return (
        "behind"
        if (ops_parsers.semver(best) or (0, 0, 0, 0)) < (ops_parsers.semver(newest) or (0, 0, 0, 0))
        else "live"
    )


async def _lotor_versions(
    gw: Gateway, section: dict[str, Any], backend: str
) -> dict[str, set[str]] | None:
    """What lotor runs, by repo; None when the container list could not be read."""
    res = await gw.call(backend, "container_list_tool", {})
    if not res.ok:
        _note_error(section, "container_list_tool", res)
        return None
    _count_ok(section)
    try:
        return _running_versions(res.json())
    except ValueError:
        _note_error(section, "container_list_tool", _bad_json(res))
        return None


def _repo_records(
    published: list[dict[str, Any]], running: dict[str, set[str]] | None
) -> list[dict[str, Any]]:
    """One record per repository released, newest version first-class."""
    by_repo: dict[str, list[dict[str, Any]]] = {}
    for item in published:
        if item.get("version"):
            by_repo.setdefault(item["repo"], []).append(item)
    records = []
    for repo, releases in by_repo.items():
        newest = max(releases, key=lambda r: ops_parsers.semver(r["version"]) or (0, 0, 0, 0))
        record: dict[str, Any] = {
            "part": PUBLISHED_PART,
            "kind": "repo",
            "repo": repo,
            "newest": newest["version"],
            "url": newest["url"],
            "published": newest.get("published"),
            "releases": len(releases),
        }
        if running is not None:
            versions = running.get(repo, set())
            record["running"] = sorted(
                versions, key=lambda v: ops_parsers.semver(v) or (0, 0, 0, 0)
            )
            record["status"] = _deploy_status(newest["version"], versions)
        records.append(record)
    return records


async def crunchtools_releases(
    gw: Gateway, _window: Window, _now: datetime, options: dict[str, Any]
) -> dict[str, Any]:
    """The releases issue joined with what lotor runs, compared in code.

    One ``repo`` record per repository released this week (newest version,
    its link and date, how many releases, the versions running, and
    ``live``/``behind``/``not-deployed``), then one record per other item of
    the issue (release pull requests, merged-not-released, refused). If the
    container list fails, the repo records carry no deploy status.
    """
    opts = ReleasesOptions(**options)
    section = _section("crunchtools_releases")
    found = await _record_issue(gw, section, opts)
    if found is None:
        return _finish(section)
    issue, flagged = found
    section["stats"]["flagged"] = flagged
    parts = ops_parsers.ashigaru_parts(issue.get("body") or "")
    section["stats"]["parts"] = _capped_parts(parts)
    running = await _lotor_versions(gw, section, opts.podman_backend)
    repos = _repo_records(parts.get(PUBLISHED_PART, {}).get("items", []), running)
    section["records"] = repos + [
        {"part": name, "kind": "item", **item, "title": None if flagged else item["title"]}
        for name, part in parts.items()
        if name != PUBLISHED_PART
        for item in part["items"]
    ]
    section["stats"]["repos_released"] = len(repos)
    if running is not None:
        statuses = [r["status"] for r in repos]
        section["stats"].update(
            live=statuses.count("live"),
            behind=statuses.count("behind"),
            not_deployed=statuses.count("not-deployed"),
        )
    return _finish(section)


# --- Source readers ------------------------------------------------------
#
# Plain reads with no "is anyone waiting" judgement: what the user sent, said,
# wrote and committed to over a window, for reports that summarize activity.


def _start(window: Window, now: datetime, lookback_days: int | None) -> datetime:
    """The read's start: ``lookback_days`` before ``now``, or the report window's start."""
    return now - timedelta(days=lookback_days) if lookback_days is not None else window.start


class GmailSearchOptions(BaseModel, extra="forbid"):
    """Options for the gmail_search collector."""

    backend: str = Field(..., min_length=1, max_length=MAX_BACKEND_NAME)
    account: str = Field(..., min_length=3, max_length=MAX_ACCOUNT_LENGTH)
    # ``{window_start}`` becomes the read's start as a Gmail date (YYYY/MM/DD).
    query: str = Field(..., min_length=1, max_length=MAX_QUERY_EXTRA)
    lookback_days: int | None = Field(default=None, ge=1, le=MAX_LOOKBACK_DAYS)
    max_threads: int = Field(default=DEFAULT_MAX_THREADS, ge=1, le=MAX_THREADS)
    body_chars: int = Field(default=DEFAULT_BODY_CHARS, ge=0, le=MAX_BODY_CHARS)
    link_template: str | None = Field(
        default="https://mail.google.com/mail/u/0/#all/{thread_id}", max_length=200
    )


async def gmail_search(
    gw: Gateway, window: Window, now: datetime, options: dict[str, Any]
) -> dict[str, Any]:
    """Every thread a Gmail query matches, one record each, nothing dropped.

    Unlike ``gmail_waiting`` there is no ownership, noise or age filtering:
    ``in:sent after:{window_start}`` returns what the user sent. A flagged
    thread keeps its id, link, dates, counts and sender address; its subject
    and body are withheld.
    """
    opts = GmailSearchOptions(**options)
    section = _section("gmail_search")
    start = _start(window, now, opts.lookback_days)
    query = opts.query.replace("{window_start}", f"{start:%Y/%m/%d}")
    section["stats"]["query"] = query
    thread_ids = await _search_threads(
        gw, opts.backend, opts.account, query, opts.max_threads, section
    )
    section["stats"]["threads_found"] = len(thread_ids)
    section["stats"]["truncated"] = max(0, len(thread_ids) - opts.max_threads)
    for tid in thread_ids[: opts.max_threads]:
        res = await gw.call(
            opts.backend,
            "get_gmail_thread_content",
            {"thread_id": tid, "user_google_email": opts.account, "include_analysis": True},
        )
        if not res.ok:
            _note_error(section, f"thread {tid}", res)
            continue
        _count_ok(section)
        facts = parsers.gmail_thread_facts(*_thread_payload(res))
        last_at: datetime | None = facts["last_at"]
        section["records"].append(
            {
                "thread_id": tid,
                "link": opts.link_template.format(thread_id=tid) if opts.link_template else None,
                "subject": None if res.flagged else facts["subject"],
                "from": parsers.email_address(facts["sender"]) if res.flagged else facts["sender"],
                "last_at": last_at.isoformat() if last_at else None,
                "age_days": _age_days(last_at, now) if last_at else None,
                "message_count": facts["message_count"],
                "participant_count": facts["participant_count"],
                "flagged": res.flagged,
                "body": None
                if res.flagged or not opts.body_chars
                else facts["body"][: opts.body_chars],
            }
        )
    return _finish(section)


class SlackSearchOptions(BaseModel, extra="forbid"):
    """Options for the slack_search collector."""

    backend: str = Field(default="slack", min_length=1, max_length=MAX_BACKEND_NAME)
    # ``{date}`` becomes the day before the read's start, so ``after:{date}``
    # (exclusive in Slack) includes the start day.
    query: str = Field(..., min_length=1, max_length=MAX_QUERY_EXTRA)
    lookback_days: int | None = Field(default=None, ge=1, le=MAX_LOOKBACK_DAYS)
    max_messages: int = Field(
        default=DEFAULT_SLACK_SEARCH_MESSAGES, ge=1, le=MAX_PAGES * SLACK_SEARCH_PAGE_SIZE
    )
    text_chars: int = Field(default=SLACK_ASK_CHARS, ge=0, le=MAX_SLACK_TEXT_CHARS)
    resolve_names: bool = True
    workspace_url: str = Field(
        default="https://redhat-internal.slack.com", min_length=8, max_length=200
    )


def _slack_where(channel: dict[str, Any]) -> str:
    """Where a search hit was posted: DM, group DM, #channel, or the bare channel id."""
    match channel:
        case {"is_im": True}:
            return "DM"
        case {"is_mpim": True}:
            return "group DM"
        case {"name": str(name)} if name:
            return f"#{name}"
        case _:
            return str(channel.get("id") or "")


def _slack_search_record(
    match: dict[str, Any], opts: SlackSearchOptions, flagged: bool
) -> dict[str, Any] | None:
    channel = match.get("channel") or {}
    ts = str(match.get("ts") or "")
    if not channel.get("id") or not ts:
        return None
    try:
        sent_at = datetime.fromtimestamp(float(ts), tz=UTC)
    except (ValueError, OverflowError, OSError):
        return None
    _, thread_ts = parsers.slack_link_parts(match.get("permalink") or "")
    return {
        "where": _slack_where(channel),
        "channel_id": channel["id"],
        "_author": match.get("user"),
        "_username": match.get("username"),
        "ts": ts,
        "sent_at": sent_at.isoformat(),
        "link": match.get("permalink")
        or parsers.slack_permalink(opts.workspace_url, channel["id"], ts, thread_ts),
        "flagged": flagged,
        "text": None
        if flagged or not opts.text_chars
        else (match.get("text") or "")[: opts.text_chars],
    }


async def slack_search(
    gw: Gateway, window: Window, now: datetime, options: dict[str, Any]
) -> dict[str, Any]:
    """Every Slack message a search query matches, one record each.

    ``from:me after:{date}`` returns what the user said. Pages of
    ``SLACK_SEARCH_PAGE_SIZE`` are read in order until a short page, the last
    page, ``max_messages`` or ``MAX_PAGES``. Messages older than the read's
    start (Slack's ``after:`` is day-granular) are dropped as
    ``dropped_before_start``. Authors are resolved to names, one lookup per
    distinct user. A flagged page's text is withheld; who, where and when stay.
    """
    opts = SlackSearchOptions(**options)
    section = _section("slack_search")
    start = _start(window, now, opts.lookback_days)
    query = opts.query.replace("{date}", (start - timedelta(days=1)).date().isoformat())
    section["stats"].update(query=query, dropped_before_start=0)
    records = await _slack_search_pages(gw, opts, query, start, section)
    section["stats"]["truncated"] = max(0, len(records) - opts.max_messages)
    records = records[: opts.max_messages]
    names: dict[str, str] = {}
    if opts.resolve_names:
        ids = list(dict.fromkeys(r["_author"] for r in records if r["_author"]))
        names = await _lookup_names(gw, opts.backend, ids, section)
    for record in records:
        uid, username = record.pop("_author"), record.pop("_username")
        record["author"] = names.get(uid or "") or username or UNKNOWN_PERSON
    section["records"] = records
    return _finish(section)


async def _slack_search_pages(
    gw: Gateway,
    opts: SlackSearchOptions,
    query: str,
    start: datetime,
    section: dict[str, Any],
) -> list[dict[str, Any]]:
    """Search records page by page, stopping at a short or last page or a cap."""
    records: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        res = await gw.call(
            opts.backend,
            "slack_search_messages",
            {
                "query": query,
                "count": SLACK_SEARCH_PAGE_SIZE,
                "sort": "timestamp",
                "sort_dir": "desc",
                "page": page,
            },
        )
        if not res.ok:
            _note_error(section, f"search p{page}", res)
            break
        try:
            payload = res.json()
        except ValueError:
            _note_error(section, f"search p{page}", _bad_json(res))
            break
        if not isinstance(payload, dict):
            _note_error(section, f"search p{page}", _bad_json(res))
            break
        _count_ok(section)
        matches = [m for m in payload.get("matches") or [] if isinstance(m, dict)]
        for match in matches:
            record = _slack_search_record(match, opts, res.flagged)
            if record is None:
                continue
            if float(record["ts"]) < start.timestamp():
                section["stats"]["dropped_before_start"] += 1
                continue
            records.append(record)
        # No usable pagination block: keep reading until a short page.
        page_count = _page_count(payload, MAX_PAGES)
        if len(matches) < SLACK_SEARCH_PAGE_SIZE or page >= page_count:
            break
        if len(records) >= opts.max_messages or page == MAX_PAGES:
            section["stats"]["search_pages_truncated"] = True
            break
    return records


class DocsSearchOptions(BaseModel, extra="forbid"):
    """Options for the docs_search collector."""

    backend: str = Field(default="gw-work", min_length=1, max_length=MAX_BACKEND_NAME)
    account: str = Field(..., min_length=3, max_length=MAX_ACCOUNT_LENGTH)
    # More Drive query, ANDed on, e.g. "'me' in owners".
    query_extra: str = Field(default="", max_length=MAX_QUERY_EXTRA)
    lookback_days: int | None = Field(default=None, ge=1, le=MAX_LOOKBACK_DAYS)
    max_docs: int = Field(default=DEFAULT_MAX_DOCS, ge=1, le=MAX_DOCS)
    link_template: str = Field(
        default="https://docs.google.com/document/d/{id}/edit", min_length=8, max_length=200
    )


async def docs_search(
    gw: Gateway, window: Window, now: datetime, options: dict[str, Any]
) -> dict[str, Any]:
    """Google Docs modified since the read's start, newest first.

    Calls workspace-mcp ``search_drive_files`` with a structured Drive query
    (``modifiedTime > '…'``) restricted to Docs and ordered by
    ``modifiedTime desc``; ``search_docs`` matches names only and cannot ask
    for a date. Paged by ``nextPageToken`` up to ``MAX_PAGES``. A flagged
    page's document names are withheld and editors reduced to an address.
    """
    opts = DocsSearchOptions(**options)
    section = _section("docs_search")
    start = _start(window, now, opts.lookback_days).astimezone(UTC)
    query = f"modifiedTime > '{start:%Y-%m-%dT%H:%M:%S}'"
    if opts.query_extra.strip():
        query += f" and ({opts.query_extra.strip()})"
    section["stats"]["query"] = query
    token: str | None = None
    for page in range(1, MAX_PAGES + 1):
        args: dict[str, Any] = {
            "user_google_email": opts.account,
            "query": query,
            "page_size": DRIVE_PAGE_SIZE,
            "file_type": "document",
            "order_by": "modifiedTime desc",
        }
        if token:
            args["page_token"] = token
        res = await gw.call(opts.backend, "search_drive_files", args)
        if not res.ok:
            _note_error(section, f"search p{page}", res)
            break
        _count_ok(section)
        files, token = parsers.drive_files(res.text)
        for doc in files:
            editor = doc["last_edited_by"]
            section["records"].append(
                {
                    "doc_id": doc["id"],
                    "name": None if res.flagged else doc["name"],
                    "modified": doc["modified"],
                    "last_edited_by": parsers.email_address(editor) if res.flagged else editor,
                    "link": opts.link_template.format(id=doc["id"]),
                    "flagged": res.flagged,
                }
            )
        if not token:
            break
        if len(section["records"]) >= opts.max_docs or page == MAX_PAGES:
            section["stats"]["search_pages_truncated"] = True
            break
    section["stats"]["truncated"] = max(0, len(section["records"]) - opts.max_docs)
    section["records"] = section["records"][: opts.max_docs]
    return _finish(section)


class WorkboardOptions(BaseModel, extra="forbid"):
    """Options for the workboard_objectives collector."""

    backend: str = Field(default="workboard", min_length=1, max_length=MAX_BACKEND_NAME)
    # Fetch these objectives instead of the ones the user's key results point to.
    objective_ids: list[int] | None = Field(
        default=None, min_length=1, max_length=MAX_WORKBOARD_OBJECTIVES
    )

    @field_validator("objective_ids")
    @classmethod
    def _check_ids(cls, value: list[int] | None) -> list[int] | None:
        if value is not None and any(oid < 1 for oid in value):
            raise ValueError("objective_ids must be positive")
        return value


def _workboard_records(objective: dict[str, Any], flagged: bool) -> list[dict[str, Any]]:
    """An objective and each of its key results, as records."""
    head = {
        "kind": "objective",
        "objective_id": objective.get("objective_id"),
        "key_result_id": None,
        "name": None if flagged else objective.get("name"),
        "progress": objective.get("progress"),
        "status": objective.get("status_color"),
        "owner": objective.get("owner"),
        "team": None if flagged else objective.get("team"),
        "dates": objective.get("dates"),
        "target_date": None,
        "last_updated": None,
        "flagged": flagged,
    }
    records = [head]
    for kr in objective.get("key_results") or []:
        if not isinstance(kr, dict):
            continue
        records.append(
            {
                **head,
                "kind": "key_result",
                "key_result_id": kr.get("key_result_id"),
                "name": None if flagged else kr.get("name"),
                "progress": kr.get("progress"),
                # WorkBoard colours objectives only; a key result's status is its progress.
                "status": None,
                "team": None,
                "dates": None,
                "target_date": kr.get("target_date") or None,
                "last_updated": kr.get("last_updated"),
            }
        )
    return records


async def workboard_objectives(
    gw: Gateway, _window: Window, _now: datetime, options: dict[str, Any]
) -> dict[str, Any]:
    """The user's WorkBoard objectives and key results, one record each.

    One call to ``workboard_get_my_objectives_tool``. Each objective record
    (``kind: objective``) is followed by its key results (``kind:
    key_result``), which carry the objective's id and owner. Objectives the
    server could not read are counted in ``stats.skipped``. Names are
    withheld from a flagged result; ids, progress, status and owner stay.
    """
    opts = WorkboardOptions(**options)
    section = _section("workboard_objectives")
    args: dict[str, Any] = {}
    if opts.objective_ids is not None:
        args["objective_ids"] = opts.objective_ids
    res = await gw.call(opts.backend, "workboard_get_my_objectives_tool", args)
    if not res.ok:
        _note_error(section, "objectives", res)
        return _finish(section)
    try:
        payload = res.json()
    except ValueError:
        _note_error(section, "objectives", _bad_json(res))
        return _finish(section)
    if isinstance(payload, dict) and payload.get("error"):
        # The server's own error string is not quoted: it is not judged text we control.
        failed = GatewayResult(text="", error="backend reported an error")
        _note_error(section, "objectives", failed)
        return _finish(section)
    _count_ok(section)
    objectives = parsers.workboard_objectives(payload)
    skipped = payload.get("skipped") if isinstance(payload, dict) else None
    section["stats"].update(
        objectives=len(objectives), skipped=len(skipped) if isinstance(skipped, list) else 0
    )
    truncated = 0
    for objective in objectives:
        room = MAX_WORKBOARD_RECORDS - len(section["records"])
        if room <= 0:
            # Count what is left without expanding it: one record plus its key results.
            truncated += 1 + len(objective.get("key_results") or [])
            continue
        records = _workboard_records(objective, res.flagged)
        section["records"].extend(records[:room])
        truncated += max(0, len(records) - room)
    section["stats"]["truncated"] = truncated
    return _finish(section)


CollectorName = Literal[
    "slack_waiting",
    "gmail_waiting",
    "calendar_day",
    "feed_entries",
    "jira_issues",
    "nagios_health",
    "ashigaru_issue",
    "crunchtools_releases",
    "gmail_search",
    "slack_search",
    "docs_search",
    "workboard_objectives",
]

Collector = Callable[["Gateway", Window, datetime, dict[str, Any]], Awaitable[dict[str, Any]]]

COLLECTORS: dict[str, Collector] = {
    "slack_waiting": slack_waiting,
    "gmail_waiting": gmail_waiting,
    "calendar_day": calendar_day,
    "feed_entries": feed_entries,
    "jira_issues": jira_issues,
    "nagios_health": nagios_health,
    "ashigaru_issue": ashigaru_issue,
    "crunchtools_releases": crunchtools_releases,
    "gmail_search": gmail_search,
    "slack_search": slack_search,
    "docs_search": docs_search,
    "workboard_objectives": workboard_objectives,
}

OPTION_MODELS: dict[str, type[BaseModel]] = {
    "slack_waiting": SlackOptions,
    "gmail_waiting": GmailOptions,
    "calendar_day": CalendarOptions,
    "feed_entries": FeedOptions,
    "jira_issues": JiraOptions,
    "nagios_health": NagiosOptions,
    "ashigaru_issue": AshigaruOptions,
    "crunchtools_releases": ReleasesOptions,
    "gmail_search": GmailSearchOptions,
    "slack_search": SlackSearchOptions,
    "docs_search": DocsSearchOptions,
    "workboard_objectives": WorkboardOptions,
}
