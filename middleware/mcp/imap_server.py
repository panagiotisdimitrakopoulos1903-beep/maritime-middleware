"""
IMAP MCP server — middleware/mcp/imap_server.py

Sole ingestion mechanism for the Maritime Middleware system (FR-04).
No Postfix Milter, Exchange Transport Agent, or client-side trigger.

Runs as its own process, communicates with Claude Code over stdio
(configured in ~/.claude/claude_desktop_config.json — see bottom of
this file for the config snippet).

Exposes exactly the four tools from PRD section 3.3:
  - get_latest_orders
  - search_orders
  - get_order
  - watch_inbox

Mode is controlled by IMAP_MODE in .env:
  IMAP_MODE=mock  -> reads from mock_inbox.py (seed data, no credentials needed)
  IMAP_MODE=live  -> reads from imap_client.py (real mailbox via IMAP_HOST etc.)

This is the only place that decision is made — nothing downstream
(parser, matcher, database, UI) knows or cares which mode is active.
"""

import os

from mcp.server.fastmcp import FastMCP

IMAP_MODE = os.environ.get("IMAP_MODE", "mock").lower()

if IMAP_MODE == "live":
    import imap_client as source
else:
    import mock_inbox as source

mcp = FastMCP("imap-mcp")


@mcp.tool()
def get_latest_orders(limit: int = 10) -> list[dict]:
    """
    Return the most recent inbound cargo order emails, newest first.

    Args:
        limit: maximum number of orders to return (default 10).
    """
    return source.get_latest_orders(limit=limit)


@mcp.tool()
def search_orders(query: str = "", sender: str = "", since_hours: int = None) -> list[dict]:
    """
    Search inbound order emails by keyword, sender, and/or recency.

    Args:
        query: text to match against subject or body (case-insensitive).
        sender: substring to match against the From address.
        since_hours: only return emails received within the last N hours.
    """
    return source.search_orders(query=query, sender=sender, since_hours=since_hours)


@mcp.tool()
def get_order(order_id: str) -> dict:
    """
    Fetch a single inbound order email by its id.

    Args:
        order_id: the id returned by get_latest_orders / search_orders.
    """
    result = source.get_order(order_id)
    if result is None:
        return {"error": f"No order found with id '{order_id}'"}
    return result


@mcp.tool()
def watch_inbox() -> dict:
    """
    Report ingestion health: current mode, whether the mailbox is
    reachable, when it was last checked, and how many messages are
    currently visible. Used by Agent 4 (Debug/Test) and Agent 5
    (Briefing) to confirm ingestion is actually working.
    """
    return source.watch_status()


if __name__ == "__main__":
    mcp.run(transport="stdio")


# ---------------------------------------------------------------------
# Add this to ~/.claude/claude_desktop_config.json under "mcpServers":
#
# "imap-mcp": {
#   "command": "python",
#   "args": ["middleware/mcp/imap_server.py"],
#   "cwd": "/absolute/path/to/maritime-middleware"
# }
# ---------------------------------------------------------------------
