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
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator

from . import parsers
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
SLACK_ASK_CHARS = 700
SLACK_TAIL_CHARS = 300
DEFAULT_LOOKBACK_DAYS = 7
MAX_LOOKBACK_DAYS = 30
DEFAULT_MAX_CONVERSATIONS = 40
MAX_CONVERSATIONS = 100
MAX_NAME_LOOKUPS = 50
UNKNOWN_PERSON = "unknown person"

# Gmail
GMAIL_SEARCH_PAGE_SIZE = 25
DEFAULT_MAX_THREADS = 60
MAX_THREADS = 200
DEFAULT_BODY_CHARS = 1200
MAX_BODY_CHARS = 4000
MAX_QUERY_EXTRA = 500

# Calendar
DEFAULT_LOOKAHEAD_DAYS = 3
MAX_LOOKAHEAD_DAYS = 14
CALENDAR_MAX_RESULTS = 100
# Free text in a parsed event, all withheld when Trentina flags the result.
FREE_TEXT_EVENT_FIELDS = (
    "title",
    "description",
    "location",
    "organizer",
    "meeting_link",
    "attendees",
)

# Feeds
MAX_FEED_CATEGORIES = 12
MAX_CATEGORY_ID_DIGITS = 9
MAX_FEED_LIMIT = 100
MAX_FEED_RECORDS = 300
MAX_SINCE_DAYS = 30
DEFAULT_SINCE_AFTER_WEEKEND = 3


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
    ones where the latest ask is ``waiting`` or ``acknowledged`` (or
    ``unverified``, when a thread was longer than the pages read). ``in_window``
    marks asks newer than the report window; older ones are the "still open"
    tail. People are resolved to names once per distinct user, after every
    conversation is read, capped at ``MAX_NAME_LOOKUPS`` calls.
    """
    opts = SlackOptions(**options)
    section = _section("slack_waiting")
    conversations = await _search_conversations(gw, opts, now, section)
    section["stats"]["conversations_found"] = len(conversations)

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
            page_count = int((payload.get("pagination") or {}).get("page_count") or 1)
            if page >= page_count:
                break
            if page == MAX_PAGES:
                section["stats"]["search_pages_truncated"] = True
    return conversations


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
    key = (channel["id"], thread_ts or (f"dm:{channel['id']}" if is_dm else ts))
    conv = conversations.setdefault(key, _new_conversation(channel, is_dm, thread_ts, ts, base))
    conv["earliest_ts"] = min(conv["earliest_ts"], float(ts))
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
        "thread_ts": thread_ts or (None if is_dm else ts),
        "earliest_ts": float(ts),
        "latest_ts": float(ts),
        "permalink_base": base,
    }


def _conversation_call(conv: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The paged read for a conversation: thread replies, or DM history from the first hit."""
    if conv["thread_ts"]:
        return "slack_get_thread_replies", {
            "channel_id": conv["channel_id"],
            "thread_ts": conv["thread_ts"],
            "limit": SLACK_THREAD_LIMIT,
        }
    return "slack_get_channel_history", {
        "channel_id": conv["channel_id"],
        "oldest": f"{conv['earliest_ts'] - 1:.6f}",
        "limit": SLACK_DM_HISTORY_LIMIT,
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
    where = f"conversation {conv['channel_id']}/{conv['thread_ts'] or 'dm'}"
    if page_failure is not None:
        _note_error(section, f"{where} later page", page_failure)
    if not res.ok:
        _note_error(section, where, res)
        return None, False
    if not messages:
        _note_error(section, where, _bad_json(res))
        return None, False
    _count_ok(section)

    state = parsers.slack_reply_state(messages, opts.user_id, conv["is_dm"])
    if state is None:
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
            if uid and uid != self_id:
                seen.setdefault(uid, None)
    return list(seen)


async def _resolve_names(
    gw: Gateway, opts: SlackOptions, records: list[dict[str, Any]], section: dict[str, Any]
) -> dict[str, str]:
    names: dict[str, str] = {}
    ids = _user_ids(records, opts.user_id)
    section["stats"]["name_lookups_skipped"] = max(0, len(ids) - MAX_NAME_LOOKUPS)
    for uid in ids[:MAX_NAME_LOOKUPS]:
        res = await gw.call(opts.backend, "slack_get_user_info", {"user_id": uid})
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
    """
    opts = GmailOptions(**options)
    section = _section("gmail_waiting")
    thread_ids = await _search_threads(gw, opts, window, section)

    stats = section["stats"]
    stats.update(
        threads_found=len(thread_ids),
        dropped_noise=0,
        dropped_not_owed=0,
        dropped_old=0,
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
    gw: Gateway, opts: GmailOptions, window: Window, section: dict[str, Any]
) -> list[str]:
    query = f"in:inbox after:{window.start:%Y/%m/%d} {opts.query_extra}".strip()
    thread_ids: list[str] = []
    token: str | None = None
    for page in range(1, MAX_PAGES + 1):
        args: dict[str, Any] = {
            "query": query,
            "user_google_email": opts.account,
            "page_size": GMAIL_SEARCH_PAGE_SIZE,
        }
        if token:
            args["page_token"] = token
        res = await gw.call(opts.backend, "search_gmail_messages", args)
        if not res.ok:
            _note_error(section, f"search p{page}", res)
            break
        _count_ok(section)
        page_ids, token = parsers.gmail_search_page(res.text)
        thread_ids += [t for t in page_ids if t not in thread_ids]
        if not token:
            break
        if len(thread_ids) >= opts.max_threads or page == MAX_PAGES:
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


def _drop_reason(facts: dict[str, Any], window: Window) -> str | None:
    """The stats key a thread is dropped under, or None to keep it."""
    if parsers.gmail_is_noise(facts["subject"], facts["sender"]):
        return "dropped_noise"
    if facts["ball"] not in (None, "user"):
        return "dropped_not_owed"
    if facts["last_at"] is not None and facts["last_at"] < window.start:
        return "dropped_old"
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
    reason = _drop_reason(facts, window)
    if reason is not None:
        stats[reason] += 1
        return None
    stats["ownership_unknown"] += facts["ball"] is None
    last_at: datetime | None = facts["last_at"]
    subject: str | None = facts["subject"]
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

    _check_timezone = field_validator("timezone")(valid_zone)


def _event_kind(event: dict[str, Any], day0: datetime) -> str | None:
    """meeting / pending_invite / all_day, or None to drop (declined, other days)."""
    start = parsers.event_start(event)
    status = event["my_status"]
    if status == "declined" or start is None:
        return None
    start_day = _local_day(start, day0)
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
    """
    opts = CalendarOptions(**options)
    section = _section("calendar_day")
    day0 = next_weekday(now, opts.timezone)
    horizon = day0 + timedelta(days=opts.lookahead_days + 1)
    res = await gw.call(
        opts.backend,
        "get_events",
        {
            "user_google_email": opts.account,
            "time_min": day0.isoformat(),
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
    for event in parsers.calendar_events(res.text, opts.account):
        kind = _event_kind(event, day0)
        if kind is None:
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

    After a weekend (Monday, or a weekend run) the lookback widens to
    ``since_days_after_weekend``. Output is capped at ``MAX_FEED_RECORDS``.
    """
    opts = FeedOptions(**options)
    section = _section("feed_entries")
    local = now.astimezone(window.start.tzinfo)
    weekend_gap = local.weekday() == 0 or local.weekday() >= SATURDAY
    since = opts.since_days_after_weekend if weekend_gap else opts.since_days
    section["stats"]["since_days"] = since
    for category, limit in opts.categories.items():
        res = await gw.call(
            opts.backend,
            "list_entries_tool",
            {
                "category_id": int(category),
                "since_days": since,
                "unread_only": False,
                "limit": limit,
            },
        )
        if not res.ok:
            _note_error(section, f"category {category}", res)
            continue
        _count_ok(section)
        try:
            entries = res.json()
        except ValueError:
            _note_error(section, f"category {category}", _bad_json(res))
            continue
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


CollectorName = Literal["slack_waiting", "gmail_waiting", "calendar_day", "feed_entries"]

Collector = Callable[["Gateway", Window, datetime, dict[str, Any]], Awaitable[dict[str, Any]]]

COLLECTORS: dict[str, Collector] = {
    "slack_waiting": slack_waiting,
    "gmail_waiting": gmail_waiting,
    "calendar_day": calendar_day,
    "feed_entries": feed_entries,
}

OPTION_MODELS: dict[str, type[BaseModel]] = {
    "slack_waiting": SlackOptions,
    "gmail_waiting": GmailOptions,
    "calendar_day": CalendarOptions,
    "feed_entries": FeedOptions,
}
