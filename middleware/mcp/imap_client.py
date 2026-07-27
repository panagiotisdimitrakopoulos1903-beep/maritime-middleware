"""
Real IMAP client. Used when IMAP_MODE=live in .env.

Kept deliberately separate from mock_inbox.py so switching from mock to
the broker's real mailbox is a config change only (IMAP_MODE, IMAP_HOST,
IMAP_PORT, IMAP_USER, IMAP_PASS in .env) — no code in imap_server.py or
downstream (parser, matcher, UI) needs to change.

Requires: IMAP_HOST, IMAP_PORT, IMAP_USER, IMAP_PASS to be set. Read-only —
this only ever reads the inbox, per PRD non-goal "will NOT send emails".
"""

import email
import imaplib
from datetime import datetime, timedelta, timezone
from email.header import decode_header

from config import settings  # pydantic-settings loader, per PRD 6.2


def _connect() -> imaplib.IMAP4_SSL:
    conn = imaplib.IMAP4_SSL(settings.imap_host, int(settings.imap_port))
    conn.login(settings.imap_user, settings.imap_pass)
    conn.select("INBOX", readonly=True)  # readonly: never mutate the mailbox
    return conn


def _decode(value) -> str:
    if value is None:
        return ""
    parts = decode_header(value)
    return "".join(
        p.decode(enc or "utf-8", errors="replace") if isinstance(p, bytes) else p
        for p, enc in parts
    )


def _extract_plain_text(msg: email.message.Message) -> str:
    """FR-03: handle MIME multipart, extract plain text body only."""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition") or "")
            if content_type == "text/plain" and "attachment" not in disposition:
                charset = part.get_content_charset() or "utf-8"
                return part.get_payload(decode=True).decode(charset, errors="replace")
        return ""
    charset = msg.get_content_charset() or "utf-8"
    return msg.get_payload(decode=True).decode(charset, errors="replace")


def _parse_message(uid: bytes, raw: bytes) -> dict:
    msg = email.message_from_bytes(raw)
    received = email.utils.parsedate_to_datetime(msg.get("Date"))
    if received.tzinfo is None:
        received = received.replace(tzinfo=timezone.utc)
    return {
        "id": uid.decode(),
        "sender": _decode(msg.get("From")),
        "subject": _decode(msg.get("Subject")),
        "received_at": received.isoformat(),
        "raw_body": _extract_plain_text(msg),
        # RFC 2822 threading headers (ADR 0004, Decision #3). Only replies
        # carry In-Reply-To/References — msg.get() returns None gracefully
        # when a header is absent, which is exactly the behavior we want
        # here rather than raising or defaulting to "". Message-ID/
        # In-Reply-To are typically a single angle-bracketed token, so
        # _decode (built for RFC 2047 encoded-word headers like Subject/
        # From) is unnecessary here; these headers are plain ASCII per
        # RFC 2822 and are returned as raw strings. References may contain
        # a space-separated list of ids (RFC 2822 §3.6.4) — captured as the
        # raw string, not parsed/split, per this slice's scope.
        "message_id": msg.get("Message-ID"),
        "in_reply_to": msg.get("In-Reply-To"),
        "references": msg.get("References"),
    }


def get_latest_orders(limit: int = 10) -> list[dict]:
    conn = _connect()
    try:
        status, data = conn.uid("search", None, "ALL")
        uids = data[0].split()[-limit:] if data and data[0] else []
        orders = []
        for uid in reversed(uids):
            status, msg_data = conn.uid("fetch", uid, "(RFC822)")
            if status == "OK" and msg_data and msg_data[0]:
                orders.append(_parse_message(uid, msg_data[0][1]))
        return orders
    finally:
        conn.logout()


def search_orders(query: str = "", sender: str = "", since_hours: int = None) -> list[dict]:
    conn = _connect()
    try:
        criteria = []
        if sender:
            criteria += ["FROM", f'"{sender}"']
        if since_hours is not None:
            since_date = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).strftime("%d-%b-%Y")
            criteria += ["SINCE", since_date]
        if not criteria:
            criteria = ["ALL"]
        status, data = conn.uid("search", None, *criteria)
        uids = data[0].split() if data and data[0] else []
        orders = []
        for uid in reversed(uids):
            status, msg_data = conn.uid("fetch", uid, "(RFC822)")
            if status == "OK" and msg_data and msg_data[0]:
                parsed = _parse_message(uid, msg_data[0][1])
                if query and query.lower() not in parsed["subject"].lower() and query.lower() not in parsed["raw_body"].lower():
                    continue
                orders.append(parsed)
        return orders
    finally:
        conn.logout()


def get_order(order_id: str) -> dict | None:
    conn = _connect()
    try:
        status, msg_data = conn.uid("fetch", order_id.encode(), "(RFC822)")
        if status == "OK" and msg_data and msg_data[0]:
            return _parse_message(order_id.encode(), msg_data[0][1])
        return None
    finally:
        conn.logout()


def watch_status() -> dict:
    """Lightweight connectivity check used by watch_inbox tool."""
    try:
        conn = _connect()
        status, data = conn.uid("search", None, "ALL")
        count = len(data[0].split()) if data and data[0] else 0
        conn.logout()
        return {
            "mode": "live",
            "watching": True,
            "last_checked": datetime.now(timezone.utc).isoformat(),
            "inbox_count": count,
        }
    except Exception as e:
        return {
            "mode": "live",
            "watching": False,
            "last_checked": datetime.now(timezone.utc).isoformat(),
            "error": str(e),
        }
