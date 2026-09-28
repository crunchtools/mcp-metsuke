"""Pure parsers for backend tool output. No I/O, so every rule here is testable.

The Google Workspace backends return formatted text rather than JSON, and Slack
returns raw API JSON far larger than anything a reader needs. These functions
turn both into small, fixed-shape records.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# --- Slack ---------------------------------------------------------------

# Slack's system user posts as a user, not a bot_id: channel removals,
# reminders, etc. Enterprise Grid workspaces use USLACK rather than USLACKBOT.
SLACK_SYSTEM_USERS = frozenset({"USLACKBOT", "USLACK"})
SLACK_SYSTEM_PROFILE = "slack"
# A direct ask: a question, or a request phrased without a question mark.
_REQUEST = re.compile(
    r"\b(?:can|could|would|will) you\b|\bplease\b|\bpls\b|\blet me know\b|\bneed your\b"
    r"|\b(?:take|have) a look\b|\b(?:your|any) (?:feedback|thoughts|input|review)\b|\bsign[- ]off\b"
    r"|\bapprove\b|\blet'?s (?:talk|meet|chat|sync|find (?:a few minutes|time))\b"
    r"|\bare you able\b|\bdo you have\b|\bwhen you get a chance\b"
    # An imperative "Review ..." opening a sentence, not the noun ("Review of X starts").
    r"|(?:^|[.!:]\s+)review\b(?!\s+(?:of|is|was|went|starts|meeting|notes)\b)",
    re.IGNORECASE,
)
# Slack markup: <@U123>, <#C123|name>, <https://...?q=1|label>.
_SLACK_MARKUP = re.compile(r"<[^<>]*>")
_THREAD_TS = re.compile(r"[?&]thread_ts=([0-9.]+)")


def slack_link_parts(permalink: str) -> tuple[str, str | None]:
    """A permalink's workspace base URL and its ``thread_ts`` parameter, if any."""
    base = permalink.split("/archives/", maxsplit=1)[0] if "/archives/" in permalink else ""
    match = _THREAD_TS.search(permalink)
    return base, match.group(1) if match else None


def slack_is_bot(message: dict[str, Any]) -> bool:
    """True for bot and app messages (Slackbot included), which never count as asks."""
    profile = message.get("user_profile") or {}
    return bool(
        message.get("user") in SLACK_SYSTEM_USERS
        or profile.get("name") == SLACK_SYSTEM_PROFILE
        or message.get("bot_id")
        or message.get("subtype") == "bot_message"
        or (message.get("user") is None and message.get("username"))
    )


def slack_is_ask(text: str | None) -> bool:
    """True when a message reads as a direct question or request.

    Slack markup (mentions, links) is removed first, so a URL's ``?`` is not a
    question, and a curly apostrophe is normalized so "Let's talk" still matches.
    """
    if not text:
        return False
    plain = _SLACK_MARKUP.sub(" ", text).replace("\u2019", "'")
    return "?" in plain or bool(_REQUEST.search(plain))


def slack_reacted_by(message: dict[str, Any], user_id: str) -> bool:
    """True when ``user_id`` left any reaction on ``message``."""
    return any(user_id in (r.get("users") or []) for r in message.get("reactions") or [])


@dataclass
class ReplyState:
    """Where a conversation stands with respect to one user."""

    last_ask: dict[str, Any]
    state: str  # waiting | answered | unverified
    tail: list[dict[str, Any]] = field(default_factory=list)


def slack_reply_state(
    messages: list[dict[str, Any]], user_id: str, is_dm: bool
) -> ReplyState | None:
    """Decide whether ``user_id`` still owes a reply in a conversation.

    An *ask* is a human message from someone else that @-mentions the user
    (tagging someone in a channel is directing it at them), or, in a DM, one
    that reads as a direct question or request (``slack_is_ask``): DM chatter
    such as "nice!" or "no worries" is not an ask. Once the user has responded,
    a conversation stays done until someone asks again. Looking only at the
    latest ask:

    - ``answered``: the user posted after it, or reacted to it.
    - ``waiting``: neither.

    Returns None when nothing in the conversation asks the user anything.
    """
    ordered = sorted(messages, key=lambda m: float(m.get("ts") or 0))
    mention = f"<@{user_id}>"

    def directed(msg: dict[str, Any]) -> bool:
        if msg.get("user") == user_id or slack_is_bot(msg):
            return False
        text = msg.get("text") or ""
        return mention in text or (is_dm and slack_is_ask(text))

    asks = [m for m in ordered if directed(m)]
    if not asks:
        return None
    last_ask = asks[-1]
    ask_ts = float(last_ask.get("ts") or 0)
    replied = any(m.get("user") == user_id and float(m.get("ts") or 0) > ask_ts for m in ordered)
    state = "answered" if replied or slack_reacted_by(last_ask, user_id) else "waiting"
    return ReplyState(last_ask=last_ask, state=state, tail=ordered[-4:])


def slack_page(payload: Any) -> tuple[list[dict[str, Any]], str | None]:
    """Messages from a history/replies response and the cursor to the next page."""
    if not isinstance(payload, dict):
        return [], None
    messages = list(payload.get("messages") or [])
    more = payload.get("has_more") and (payload.get("response_metadata") or {}).get("next_cursor")
    return messages, str(more) if more else None


def slack_permalink(base: str, channel_id: str, ts: str, thread_ts: str | None) -> str:
    """Build a message permalink in the workspace ``base`` URL."""
    link = f"{base}/archives/{channel_id}/p{ts.replace('.', '')}"
    if thread_ts and thread_ts != ts:
        link += f"?thread_ts={thread_ts}"
    return link


def slack_real_name(payload: Any) -> str | None:
    """Pull a display name from a users.info response, whatever its nesting."""
    user = payload.get("user", payload) if isinstance(payload, dict) else {}
    if not isinstance(user, dict):
        return None
    profile = user.get("profile") or {}
    for value in (
        user.get("real_name"),
        profile.get("real_name"),
        profile.get("display_name"),
        user.get("name"),
    ):
        if value:
            return str(value)
    return None


# --- Gmail ---------------------------------------------------------------

_THREAD_ID = re.compile(r"Thread ID: (\S+)")
_PAGE_TOKEN = re.compile(r"page_token='([^']+)'")
_MESSAGE_SPLIT = re.compile(r"^=== Message \d+ ===$", re.MULTILINE)
_WROTE = re.compile(r"^On .{5,200}wrote:\s*$", re.MULTILINE | re.DOTALL)
_SIGNATURE = re.compile(r"^--\s*$", re.MULTILINE)

AUTOMATED_SENDER = re.compile(
    r"(no-?reply|do-?not-?reply|notifications?@|mailer-daemon|jira@|github\.com|"
    r"calendar-notification|@calendar\.google\.com|bounce)",
    re.IGNORECASE,
)
CALENDAR_NOTICE = re.compile(
    r"^(accepted|declined|tentatively accepted|canceled|cancelled|"
    r"updated invitation|invitation updated|new event)\b",
    re.IGNORECASE,
)


def gmail_search_page(text: str) -> tuple[list[str], str | None]:
    """Thread IDs (de-duplicated, in order) and the next page token of a search result."""
    seen: dict[str, None] = {}
    for tid in _THREAD_ID.findall(text or ""):
        seen.setdefault(tid, None)
    token = _PAGE_TOKEN.search(text or "")
    return list(seen), token.group(1) if token else None


def _header(block: str, name: str) -> str | None:
    match = re.search(rf"^{name}: (.*)$", block, re.MULTILINE)
    return match.group(1).strip() if match else None


def _new_text(body: str) -> str:
    """A message body minus the quoted reply chain and the signature, one line."""
    body = body.replace("\r\n", "\n")
    for marker in (_WROTE, _SIGNATURE):
        found = marker.search(body)
        body = body[: found.start()] if found else body
    kept = (ln for ln in body.splitlines() if not ln.lstrip().startswith(">"))
    return re.sub(r"\s+", " ", " ".join(kept)).strip()


def gmail_latest_message(content: str) -> dict[str, str | None]:
    """Subject plus the newest message's sender and new text (quotes stripped)."""
    last = _MESSAGE_SPLIT.split(content)[-1]
    _, _, body = last.partition("\n\n")
    return {
        "subject": _header(content, "Subject"),
        "from": _header(last, "From"),
        "body": _new_text(body),
    }


def gmail_thread_facts(content: str, analysis: dict[str, Any]) -> dict[str, Any]:
    """Everything the sweep decides on for one thread, from its text and analysis.

    The backend's analysis wins where it has an answer (subject, last sender);
    the parsed text fills the gaps. ``ball`` is ``ball_in_court_of`` (``user``
    means the user owes a reply) or None when the backend gave no analysis.
    """
    latest = gmail_latest_message(content)
    last_ts = analysis.get("last_timestamp")
    return {
        "subject": latest["subject"] or analysis.get("thread_subject"),
        "sender": analysis.get("last_sender") or latest["from"],
        "ball": analysis.get("ball_in_court_of"),
        "last_at": datetime.fromisoformat(last_ts) if last_ts else None,
        "body": latest["body"] or "",
        "message_count": analysis.get("message_count"),
        "participant_count": len(analysis.get("participants") or []),
    }


_ADDRESS = re.compile(r"<([^<>@\s]+@[^<>\s]+)>")
_BARE_ADDRESS = re.compile(r"[^<>@\s]+@[^<>@\s]+")


def email_address(sender: str | None) -> str | None:
    """Just the address from ``Name <addr>``, or a bare address; else None.

    Used for flagged results, so it never returns sender-chosen free text: a
    value with no parseable address yields None.
    """
    if not sender:
        return None
    match = _ADDRESS.search(sender)
    if match:
        return match.group(1)
    bare = sender.strip()
    return bare if _BARE_ADDRESS.fullmatch(bare) else None


def gmail_is_noise(subject: str | None, sender: str | None) -> bool:
    """Automated mail and bare calendar notices; invitations are not noise."""
    if sender and AUTOMATED_SENDER.search(sender):
        return True
    return bool(subject and CALENDAR_NOTICE.search(subject))


# --- Calendar ------------------------------------------------------------

_EVENT_HEAD = re.compile(r'^- "(?P<title>.*)" \(Starts: (?P<start>[^,]+), Ends: (?P<end>[^)]+)\)$')
_TAG = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", text))).strip()


def calendar_events(text: str, account: str) -> list[dict[str, Any]]:
    """Parse get_events(detailed=True) text into event dicts.

    ``my_status`` is the account's own response (accepted, tentative,
    needsAction, declined); events the account organized with no attendee list
    count as accepted.
    """
    events: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    field_name: str | None = None
    for raw in (text or "").splitlines():
        head = _EVENT_HEAD.match(raw)
        if head:
            current = {
                "title": head["title"],
                "start": head["start"].strip(),
                "end": head["end"].strip(),
                "description": "",
                "attendee_details": [],
            }
            events.append(current)
            field_name = None
            continue
        if current is None:
            continue
        field_name = _calendar_line(current, raw.strip(), field_name)
    for event in events:
        event["my_status"] = _my_status(event, account)
        event["attendee_count"] = len(
            [d for d in event.pop("attendee_details") if d and d != "None"]
        )
        desc = event.get("description", "")
        event["description"] = "" if desc.strip() == "No Description" else _strip_html(desc)[:300]
        event["all_day"] = "T" not in event["start"]
    return events


_EVENT_FIELDS = ("Description", "Location", "Organizer", "Meeting Link", "Attendees")


def _calendar_line(event: dict[str, Any], line: str, field_name: str | None) -> str | None:
    """Fold one line into ``event``; returns the field a continuation line belongs to."""
    for key in _EVENT_FIELDS:
        if line.startswith(f"{key}: "):
            event[key.lower().replace(" ", "_")] = line[len(key) + 2 :]
            return key
    if line.startswith("Attendee Details: "):
        event["attendee_details"].append(line[len("Attendee Details: ") :])
        return "Attendee Details"
    if line.startswith("ID: ") and "| Link: " in line:
        event["link"] = line.split("| Link: ", 1)[1].strip()
        return None
    if line and field_name == "Attendee Details":
        event["attendee_details"].append(line)
    elif line and field_name == "Description":
        event["description"] += " " + line
    return field_name


def _my_status(event: dict[str, Any], account: str) -> str:
    details: list[str] = event["attendee_details"]
    for detail in details:
        if detail.startswith(f"{account}:"):
            return detail.split(":", 1)[1].strip().split(" ")[0]
    return "accepted"


def event_start(event: dict[str, Any]) -> datetime | None:
    """An event dict's ``start`` as a datetime (naive date for all-day), or None if unparseable."""
    try:
        return datetime.fromisoformat(event["start"])
    except (KeyError, ValueError):
        return None


def event_end(event: dict[str, Any]) -> datetime | None:
    """An event dict's ``end`` as a datetime (naive date for all-day), or None if unparseable."""
    try:
        return datetime.fromisoformat(event["end"])
    except (KeyError, ValueError):
        return None
