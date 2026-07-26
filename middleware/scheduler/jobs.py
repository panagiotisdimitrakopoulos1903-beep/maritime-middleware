"""
scheduler/jobs.py — APScheduler background jobs.

Runs the Signal Ocean cache refresh and the IMAP inbox poll on fixed
intervals, both independent of each other and of any live request.
"""
import importlib
import os
import sys
import uuid

import structlog
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from config import settings
from database.models import get_engine, get_session, InboundOrder
from signal_client.client import SignalCacheClient

log = structlog.get_logger()

_scheduler = BackgroundScheduler(timezone="UTC")
_engine = None

# middleware/mcp/ holds mock_inbox.py / imap_client.py (the same modules
# imap_server.py wraps as MCP tools). That directory is added to sys.path
# directly and the bare module names are imported below — NOT
# `from mcp import mock_inbox` / `import mcp.mock_inbox` — because `mcp` is
# also the name of the installed MCP SDK package (imap_server.py does
# `from mcp.server.fastmcp import FastMCP`), and a package-style import
# here would resolve against that SDK package instead of this directory.
_MCP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp")


def _load_imap_source():
    """
    Mode-select between mock_inbox.py and imap_client.py, exactly the way
    mcp/imap_server.py does it (bare IMAP_MODE env read, not a Settings
    field — see config.py's IMAP ingestion section).
    """
    if _MCP_DIR not in sys.path:
        sys.path.insert(0, _MCP_DIR)

    mode = os.environ.get("IMAP_MODE", "mock").lower()
    module_name = "imap_client" if mode == "live" else "mock_inbox"
    return importlib.import_module(module_name)


def _refresh_signal_cache():
    """
    Job function called by APScheduler.
    Creates its own DB session (scheduler runs in separate thread).
    """
    global _engine
    if _engine is None:
        _engine = get_engine(settings.database_url)

    session: Session = get_session(_engine)
    try:
        client = SignalCacheClient(session)
        count = client.refresh()
        log.info("scheduler.signal_refresh_done", vessel_count=count)
    except Exception as e:
        log.error("scheduler.signal_refresh_error", error=str(e))
    finally:
        session.close()


def _poll_imap_inbox():
    """
    Job function called by APScheduler.

    Fetches the latest mailbox messages (mock or live, per IMAP_MODE) and
    feeds any not-yet-seen ones through the existing `_process_inbound`
    pipeline, deduping on `InboundOrder.source_message_id`. Reuses the
    pipeline in-process rather than duplicating it or looping back over
    HTTP to /internal/ingest (ADR 0002).
    """
    global _engine
    if _engine is None:
        _engine = get_engine(settings.database_url)

    # Deferred import: api.app imports scheduler.jobs at module level
    # (start_scheduler/stop_scheduler), so importing api.app back at
    # jobs.py's module level would be circular. Safe once both modules
    # have finished loading, i.e. inside the job function itself.
    from api.app import _process_inbound

    source = _load_imap_source()

    try:
        messages = source.get_latest_orders(limit=settings.imap_poll_batch_size)
    except Exception as e:
        log.error("scheduler.imap_poll_fetch_error", error=str(e))
        return

    if not messages:
        log.info("scheduler.imap_poll_done", fetched=0, new_messages=0)
        return

    session: Session = get_session(_engine)
    try:
        message_ids = [m["id"] for m in messages]
        existing_ids = {
            row[0]
            for row in session.query(InboundOrder.source_message_id)
            .filter(InboundOrder.source_message_id.in_(message_ids))
            .all()
        }

        new_messages = [m for m in messages if m["id"] not in existing_ids]
        new_messages.sort(key=lambda m: m["received_at"])  # oldest-first

        for msg in new_messages:
            _process_inbound(
                msg["sender"],
                msg["subject"],
                msg["raw_body"],
                uuid.uuid4(),
                source_message_id=msg["id"],
            )

        log.info(
            "scheduler.imap_poll_done",
            fetched=len(messages),
            new_messages=len(new_messages),
        )
    except Exception as e:
        log.error("scheduler.imap_poll_error", error=str(e))
    finally:
        session.close()


def start_scheduler():
    """Start the background scheduler. Call once on application startup."""
    _scheduler.add_job(
        func=_refresh_signal_cache,
        trigger=IntervalTrigger(minutes=settings.signal_cache_refresh_minutes),
        id="signal_cache_refresh",
        name="Signal Ocean cache refresh",
        replace_existing=True,
        max_instances=1,          # never run two refreshes simultaneously
        coalesce=True,            # if a run was missed, run once only
    )
    _scheduler.add_job(
        func=_poll_imap_inbox,
        trigger=IntervalTrigger(minutes=settings.imap_poll_interval_minutes),
        id="imap_inbox_poll",
        name="IMAP inbox poll",
        replace_existing=True,
        max_instances=1,          # never run two polls simultaneously
        coalesce=True,            # if a run was missed, run once only
    )
    _scheduler.start()
    log.info(
        "scheduler.started",
        refresh_interval_minutes=settings.signal_cache_refresh_minutes,
        imap_poll_interval_minutes=settings.imap_poll_interval_minutes,
    )

    # Run immediately on startup so cache/mailbox are populated before the
    # first live request.
    _refresh_signal_cache()
    _poll_imap_inbox()


def stop_scheduler():
    """Gracefully stop the scheduler on application shutdown."""
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("scheduler.stopped")
