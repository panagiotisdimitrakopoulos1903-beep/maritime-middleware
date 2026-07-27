"""
Mock inbox data source for the IMAP MCP server.

Used when IMAP_MODE=mock (see .env). Lets Agents 3/4 build and test the
full ingestion -> parse -> match -> UI pipeline before the broker's real
IMAP credentials (OQ-02/OQ-03) are available. Swapping IMAP_MODE=live in
.env switches to imap_client.py with zero code changes elsewhere.
"""

from datetime import datetime, timedelta, timezone

_NOW = datetime.now(timezone.utc)


def _hours_ago(h: int) -> str:
    return (_NOW - timedelta(hours=h)).isoformat()


# Five sample inbound cargo orders, matching the format broker messages
# actually arrive in (abbreviated shipping shorthand, per FR-09).
MOCK_EMAILS = [
    {
        "id": "mock-0001",
        "sender": "chartering@graintraders-eu.com",
        "subject": "FFA - 55K GRAIN ANT/JPN LC AUG 10-20",
        "received_at": _hours_ago(1),
        "raw_body": (
            "55k grain ant/jpn lc aug 10-20\n"
            "bss 1sb/1sb\n"
            "otherwise as main terms\n"
            "pls advise avails"
        ),
        "message_id": "<mock-0001.20260726T010000@graintraders-eu.com>",
        "in_reply_to": None,
        "references": None,
    },
    {
        "id": "mock-0002",
        "sender": "ops@petrochem-shipping.com",
        "subject": "ENQ - 80,000mt Crude Ras Tanura / Rotterdam",
        "received_at": _hours_ago(3),
        "raw_body": (
            "80,000mt crude oil\n"
            "load: ras tanura\n"
            "disch: rotterdam\n"
            "laycan 15-20 aug\n"
            "aframax pref\n"
            "rgds"
        ),
        "message_id": "<mock-0002.20260725T230000@petrochem-shipping.com>",
        "in_reply_to": None,
        "references": None,
    },
    {
        "id": "mock-0003",
        "sender": "j.miller@coastalgrain.co.uk",
        "subject": "60kt wheat enquiry - odessa/alexandria",
        "received_at": _hours_ago(5),
        "raw_body": (
            "60kt wheat\n"
            "odessa / alexandria\n"
            "lc first half sept\n"
            "handymax stem\n"
            "any avails appreciated"
        ),
        "message_id": "<mock-0003.20260725T210000@coastalgrain.co.uk>",
        "in_reply_to": None,
        "references": None,
    },
    {
        "id": "mock-0004",
        "sender": "chartering@atlantic-bulkers.com",
        "subject": "150,000mt iron ore - tubarao/qingdao",
        "received_at": _hours_ago(8),
        "raw_body": (
            "150,000mt iron ore\n"
            "tubarao to qingdao\n"
            "laycan 1-10 sept\n"
            "capesize\n"
            "pls send options"
        ),
        "message_id": "<mock-0004.20260725T180000@atlantic-bulkers.com>",
        "in_reply_to": None,
        "references": None,
    },
    {
        # Reply to mock-0002 — exercises thread-grouping logic end to end
        # (ADR 0004, Decision #3 build item #2): in_reply_to here matches
        # mock-0002's message_id, so ingest-time thread_id computation
        # should fold this row into mock-0002's thread rather than starting
        # a new one. references carries the same id, per RFC 2822 §3.6.4
        # (a single-id reference chain is the common case for a first
        # reply).
        "id": "mock-0005",
        "sender": "desk3@euro-tankers.com",
        "subject": "RE: ENQ - 80,000mt Crude Ras Tanura / Rotterdam",
        "received_at": _hours_ago(20),
        "raw_body": (
            "re below - can offer aframax open ras tanura early aug\n"
            "let us know if still workable"
        ),
        "message_id": "<mock-0005.20260725T060000@euro-tankers.com>",
        "in_reply_to": "<mock-0002.20260725T230000@petrochem-shipping.com>",
        "references": "<mock-0002.20260725T230000@petrochem-shipping.com>",
    },
]


def get_latest_orders(limit: int = 10):
    ordered = sorted(MOCK_EMAILS, key=lambda e: e["received_at"], reverse=True)
    return ordered[:limit]


def search_orders(query: str = "", sender: str = "", since_hours: int = None):
    results = MOCK_EMAILS
    if query:
        q = query.lower()
        results = [e for e in results if q in e["subject"].lower() or q in e["raw_body"].lower()]
    if sender:
        results = [e for e in results if sender.lower() in e["sender"].lower()]
    if since_hours is not None:
        cutoff = _NOW - timedelta(hours=since_hours)
        results = [e for e in results if datetime.fromisoformat(e["received_at"]) >= cutoff]
    return sorted(results, key=lambda e: e["received_at"], reverse=True)


def get_order(order_id: str):
    for e in MOCK_EMAILS:
        if e["id"] == order_id:
            return e
    return None


def watch_status():
    """Mock version just reports as if polling is healthy."""
    return {
        "mode": "mock",
        "watching": True,
        "last_checked": _NOW.isoformat(),
        "inbox_count": len(MOCK_EMAILS),
    }
