"""
Signal Ocean MCP server — middleware/mcp/signal_server.py

Second of the two MCP servers from PRD section 3.3. Runs as its own
process, communicates with Claude Code over stdio (configured in
~/.claude/claude_desktop_config.json — see bottom of this file).

Exposes exactly the five tools from PRD section 3.3:
  - get_tonnage_list
  - get_vessel
  - get_ports
  - get_distances
  - get_vessel_classes

Mode is controlled by SIGNAL_MODE in .env:
  SIGNAL_MODE=mock  -> reads from mock_vessels.py (14 seed vessels, no API key needed)
  SIGNAL_MODE=live  -> reads from signal_client.py (real Signal Ocean API,
                       via the local PostgreSQL cache — see FR-15)

This is the only place that decision is made — the matching engine and
everything downstream doesn't know or care which mode is active.
"""

import os

from mcp.server.fastmcp import FastMCP

SIGNAL_MODE = os.environ.get("SIGNAL_MODE", "mock").lower()

if SIGNAL_MODE == "live":
    import signal_client as source
else:
    import mock_vessels as source

mcp = FastMCP("signal-ocean-mcp")


@mcp.tool()
def get_tonnage_list(vessel_class: str = None, area: str = None, cargo_type: str = None) -> list[dict]:
    """
    Return available vessels from the local Signal Ocean cache, optionally
    filtered by class, open-port area, and/or cargo type.

    Args:
        vessel_class: e.g. 'Handymax', 'Panamax', 'Capesize', 'Aframax', 'Suezmax', 'VLCC'.
        area: substring match against the vessel's open port area (e.g. 'ARA', 'Far East').
        cargo_type: substring match against the vessel's supported cargo types.
    """
    return source.get_tonnage_list(vessel_class=vessel_class, area=area, cargo_type=cargo_type)


@mcp.tool()
def get_vessel(vessel_id: str) -> dict:
    """
    Fetch full detail for a single vessel by id.

    Args:
        vessel_id: the vessel_id returned by get_tonnage_list.
    """
    result = source.get_vessel(vessel_id)
    if result is None:
        return {"error": f"No vessel found with id '{vessel_id}'"}
    return result


@mcp.tool()
def get_ports(query: str = None) -> list[dict]:
    """
    Look up canonical port names and their trading area, for normalising
    raw port names parsed from broker emails (FR-07).

    Args:
        query: optional substring to filter by port name or area.
    """
    return source.get_ports(query=query)


@mcp.tool()
def get_distances(from_port: str, to_port: str) -> dict:
    """
    Get the sea distance in nautical miles between two ports, for
    geography scoring (PRD 9.2).

    Args:
        from_port: canonical name of the origin port.
        to_port: canonical name of the destination port.
    """
    return source.get_distances(from_port, to_port)


@mcp.tool()
def get_vessel_classes() -> list[str]:
    """Return the full list of vessel classes the system recognises."""
    return source.get_vessel_classes()


@mcp.tool()
def refresh_status() -> dict:
    """
    Report Signal Ocean cache health: mode, last refresh time, vessel
    count, success/failure, duration. Used by Agent 4 (Debug/Test) and
    Agent 5 (Briefing) — corresponds to FR-16/FR-27.
    """
    return source.refresh_status()


if __name__ == "__main__":
    mcp.run(transport="stdio")


# ---------------------------------------------------------------------
# Add this to ~/.claude/claude_desktop_config.json under "mcpServers"
# (alongside imap-mcp):
#
# "signal-ocean-mcp": {
#   "command": "python",
#   "args": ["middleware/mcp/signal_server.py"],
#   "cwd": "/absolute/path/to/maritime-middleware"
# }
# ---------------------------------------------------------------------
