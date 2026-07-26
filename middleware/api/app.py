"""
api/app.py — FastAPI application.

Endpoints:
  POST /internal/ingest      — called by Milter with raw email body
  GET  /matches/{order_id}   — get ranked matches for an order
  GET  /orders/latest        — latest N orders with their matches
  GET  /status               — system health (cache age, vessel count)
  WS   /ws                   — WebSocket push to Electron panel
"""
import asyncio
import concurrent.futures
import uuid
import structlog
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import settings
from database.models import (
    get_engine, get_session, create_tables,
    InboundOrder, MatchResult, CachedVessel
)
from parser.llm_parser import parse_message
from signal_client.client import SignalCacheClient
from matching.engine import MatchingEngine, ScoredVessel
from scheduler.jobs import start_scheduler, stop_scheduler

log = structlog.get_logger()

# ── Application state ─────────────────────────────────────────────────────────

engine = get_engine(settings.database_url)
matching_engine = MatchingEngine()
active_websockets: list[WebSocket] = []
_main_event_loop: Optional[asyncio.AbstractEventLoop] = None


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    log.info("app.starting")
    global _main_event_loop
    _main_event_loop = asyncio.get_running_loop()
    create_tables(engine)
    start_scheduler()
    yield
    stop_scheduler()
    log.info("app.stopped")


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "app://"],   # Electron origins
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / response schemas ────────────────────────────────────────────────

class IngestRequest(BaseModel):
    sender: str
    subject: str
    raw_body: str
    source_message_id: Optional[str] = None


class MatchResponse(BaseModel):
    order_id: str
    received_at: datetime
    sender: Optional[str]
    subject: Optional[str]

    # Parsed fields
    cargo_type: Optional[str]
    quantity_mt: Optional[float]
    load_port: Optional[str]
    load_port_canonical: Optional[str]
    discharge_port: Optional[str]
    discharge_port_canonical: Optional[str]
    laycan_start: Optional[str]
    laycan_end: Optional[str]
    vessel_type: Optional[str]

    # Parse quality
    parse_confidence: float
    has_low_confidence_fields: bool
    low_confidence_fields: list[str]

    # Ranked matches
    matches: list[dict]

    # Cache info
    signal_last_refreshed: Optional[datetime]


class StatusResponse(BaseModel):
    status: str
    vessel_count: int
    signal_last_refreshed: Optional[datetime]
    signal_cache_age_minutes: Optional[float]
    pending_orders: int
    uptime_seconds: float


# ── Core processing pipeline ──────────────────────────────────────────────────

def _process_inbound(
    sender: str,
    subject: str,
    raw_body: str,
    order_id: uuid.UUID,
    source_message_id: Optional[str] = None,
):
    """
    Full processing pipeline:
    1. Parse the message
    2. Run matching against Signal cache
    3. Persist everything to DB
    4. Push results to connected WebSocket clients

    `source_message_id` carries the IMAP UID / mock id through for
    MCP-sourced messages so the IMAP poller (scheduler/jobs.py) can dedupe
    repeated polls of the same mailbox. Milter-era / manual callers omit it.
    """
    session: Session = get_session(engine)
    try:
        log.info("pipeline.start", order_id=str(order_id))

        # ── Step 1: Parse ─────────────────────────────────────────────────────
        parsed = parse_message(raw_body)

        # ── Step 2: Persist order ─────────────────────────────────────────────
        order_row = InboundOrder(
            id=order_id,
            sender=sender,
            subject=subject,
            raw_body=raw_body,
            source_message_id=source_message_id,
            cargo_type=str(parsed.cargo_type.value) if parsed.cargo_type.value else None,
            quantity_mt=float(parsed.quantity_mt.value) if parsed.quantity_mt.value else None,
            quantity_min_mt=float(parsed.quantity_min_mt.value) if parsed.quantity_min_mt.value else None,
            quantity_max_mt=float(parsed.quantity_max_mt.value) if parsed.quantity_max_mt.value else None,
            load_port=str(parsed.load_port.value) if parsed.load_port.value else None,
            load_port_canonical=parsed.load_port_canonical,
            discharge_port=str(parsed.discharge_port.value) if parsed.discharge_port.value else None,
            discharge_port_canonical=parsed.discharge_port_canonical,
            laycan_start=_parse_dt(parsed.laycan_start.value),
            laycan_end=_parse_dt(parsed.laycan_end.value),
            vessel_size_dwt=float(parsed.vessel_size_dwt.value) if parsed.vessel_size_dwt.value else None,
            vessel_type=str(parsed.vessel_type.value) if parsed.vessel_type.value else None,
            freight_rate=str(parsed.freight_rate.value) if parsed.freight_rate.value else None,
            charterer=str(parsed.charterer.value) if parsed.charterer.value else None,
            confidence_scores={
                f: getattr(parsed, f).confidence
                for f in [
                    "cargo_type", "quantity_mt", "load_port",
                    "discharge_port", "laycan_start", "laycan_end"
                ]
            },
            parse_confidence=parsed.parse_confidence,
            has_low_confidence_fields=parsed.has_low_confidence_fields,
            parse_error=parsed.error,
        )
        session.add(order_row)
        session.commit()

        # ── Step 3: Match ─────────────────────────────────────────────────────
        signal_client = SignalCacheClient(session)
        vessels = signal_client.get_available_vessels()
        ranked = matching_engine.rank(vessels, parsed)

        # ── Step 4: Persist matches ───────────────────────────────────────────
        for sv in ranked:
            session.add(MatchResult(
                order_id=order_id,
                vessel_id=sv.vessel_id,
                vessel_name=sv.vessel_name,
                vessel_class=sv.vessel_class,
                dwt=sv.dwt,
                open_port=sv.open_port,
                open_date=sv.open_date,
                rank=sv.rank,
                total_score=sv.total_score,
                score_vessel_size=sv.score_vessel_size,
                score_geography=sv.score_geography,
                score_date_overlap=sv.score_date_overlap,
                score_cargo_type=sv.score_cargo_type,
            ))
        session.commit()

        # ── Step 5: Push to WebSocket clients ─────────────────────────────────
        _push_to_websockets(order_id, parsed, ranked, signal_client.get_last_refresh())

        log.info(
            "pipeline.complete",
            order_id=str(order_id),
            matches=len(ranked),
            top_score=ranked[0].total_score if ranked else None
        )

    except Exception as e:
        log.error("pipeline.error", order_id=str(order_id), error=str(e))
        session.rollback()
    finally:
        session.close()


def _parse_dt(val) -> Optional[datetime]:
    if not val:
        return None
    try:
        return datetime.fromisoformat(str(val)).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _push_to_websockets(
    order_id: uuid.UUID,
    parsed,
    ranked: list[ScoredVessel],
    last_refresh: Optional[datetime],
):
    """Push match results to all connected Electron panels."""
    if not active_websockets:
        return

    payload = {
        "type": "NEW_MATCHES",
        "order_id": str(order_id),
        "parsed": {
            "cargo_type": parsed.cargo_type.value,
            "cargo_type_confidence": parsed.cargo_type.confidence,
            "quantity_mt": parsed.quantity_mt.value,
            "load_port": parsed.load_port_canonical or parsed.load_port.value,
            "load_port_confidence": parsed.load_port.confidence,
            "discharge_port": parsed.discharge_port_canonical or parsed.discharge_port.value,
            "discharge_port_confidence": parsed.discharge_port.confidence,
            "laycan_start": parsed.laycan_start.value,
            "laycan_start_confidence": parsed.laycan_start.confidence,
            "laycan_end": parsed.laycan_end.value,
            "laycan_end_confidence": parsed.laycan_end.confidence,
            "parse_confidence": parsed.parse_confidence,
            "has_low_confidence_fields": parsed.has_low_confidence_fields,
            "low_confidence_fields": parsed.low_confidence_field_names,
        },
        "matches": [
            {
                "rank": sv.rank,
                "vessel_name": sv.vessel_name,
                "vessel_class": sv.vessel_class,
                "dwt": sv.dwt,
                "open_port": sv.open_port,
                "open_port_area": sv.open_port_area,
                "open_date": sv.open_date.isoformat() if sv.open_date else None,
                "total_score": sv.total_score,
                "score_breakdown": {
                    "vessel_size": sv.score_vessel_size,
                    "geography": sv.score_geography,
                    "date_overlap": sv.score_date_overlap,
                    "cargo_type": sv.score_cargo_type,
                }
            }
            for sv in ranked
        ],
        "signal_last_refreshed": last_refresh.isoformat() if last_refresh else None,
    }

    if _main_event_loop is None or _main_event_loop.is_closed():
        log.warning("websocket.broadcast_skipped", reason="no_main_event_loop")
        return

    try:
        future = asyncio.run_coroutine_threadsafe(
            _broadcast_new_matches(payload), _main_event_loop
        )
    except RuntimeError as e:
        log.warning("websocket.broadcast_schedule_failed", error=str(e))
        return

    future.add_done_callback(_log_broadcast_outcome)


async def _broadcast_new_matches(payload: dict) -> None:
    """
    Send `payload` to every connected Electron panel.

    Must only ever be invoked via `asyncio.run_coroutine_threadsafe` targeting
    `_main_event_loop` — never called directly — so all reads/writes of
    `active_websockets` happen on the main loop thread, which is what makes
    this safe to run alongside `websocket_endpoint`'s connect/disconnect
    handling without a lock (confinement, not locking).
    """
    dead = []
    for ws in active_websockets:
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        active_websockets.remove(ws)


def _log_broadcast_outcome(future: concurrent.futures.Future) -> None:
    """Logging-only done-callback for a scheduled broadcast. Must not raise."""
    if future.exception() is not None:
        log.error("websocket.broadcast_failed", error=str(future.exception()))


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/internal/ingest", status_code=202)
async def ingest(req: IngestRequest, background_tasks: BackgroundTasks):
    """
    Called by the Milter hook for every inbound email.
    Returns immediately (202 Accepted) and processes in background.
    """
    order_id = uuid.uuid4()
    background_tasks.add_task(
        _process_inbound,
        req.sender,
        req.subject,
        req.raw_body,
        order_id,
        source_message_id=req.source_message_id,
    )
    log.info("ingest.accepted", order_id=str(order_id))
    return {"order_id": str(order_id), "status": "accepted"}


@app.get("/matches/{order_id}", response_model=MatchResponse)
async def get_matches(order_id: str):
    """
    Retrieve ranked matches for a specific order.
    Called by the Electron panel when the broker clicks a message.
    """
    session: Session = get_session(engine)
    try:
        order = session.query(InboundOrder).filter_by(id=order_id).first()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        matches = (
            session.query(MatchResult)
            .filter_by(order_id=order_id)
            .order_by(MatchResult.rank)
            .all()
        )

        signal_client = SignalCacheClient(session)
        last_refresh = signal_client.get_last_refresh()

        return MatchResponse(
            order_id=str(order.id),
            received_at=order.received_at,
            sender=order.sender,
            subject=order.subject,
            cargo_type=order.cargo_type,
            quantity_mt=order.quantity_mt,
            load_port=order.load_port,
            load_port_canonical=order.load_port_canonical,
            discharge_port=order.discharge_port,
            discharge_port_canonical=order.discharge_port_canonical,
            laycan_start=str(order.laycan_start) if order.laycan_start else None,
            laycan_end=str(order.laycan_end) if order.laycan_end else None,
            vessel_type=order.vessel_type,
            parse_confidence=order.parse_confidence or 0.0,
            has_low_confidence_fields=order.has_low_confidence_fields or False,
            low_confidence_fields=list(order.confidence_scores.keys())
            if order.confidence_scores else [],
            matches=[
                {
                    "rank": m.rank,
                    "vessel_name": m.vessel_name,
                    "vessel_class": m.vessel_class,
                    "dwt": m.dwt,
                    "open_port": m.open_port,
                    "open_date": m.open_date.isoformat() if m.open_date else None,
                    "total_score": m.total_score,
                    "score_breakdown": {
                        "vessel_size": m.score_vessel_size,
                        "geography": m.score_geography,
                        "date_overlap": m.score_date_overlap,
                        "cargo_type": m.score_cargo_type,
                    }
                }
                for m in matches
            ],
            signal_last_refreshed=last_refresh,
        )
    finally:
        session.close()


@app.get("/orders/latest")
async def get_latest_orders(limit: int = 20):
    """
    Return the most recent orders with their top match.
    Used by the Electron panel to populate the order list.
    """
    session: Session = get_session(engine)
    try:
        orders = (
            session.query(InboundOrder)
            .order_by(InboundOrder.received_at.desc())
            .limit(limit)
            .all()
        )
        result = []
        for order in orders:
            top_match = (
                session.query(MatchResult)
                .filter_by(order_id=order.id)
                .order_by(MatchResult.rank)
                .first()
            )
            result.append({
                "order_id": str(order.id),
                "received_at": order.received_at.isoformat(),
                "sender": order.sender,
                "subject": order.subject,
                "cargo_type": order.cargo_type,
                "load_port_canonical": order.load_port_canonical,
                "discharge_port_canonical": order.discharge_port_canonical,
                "parse_confidence": order.parse_confidence,
                "has_low_confidence_fields": order.has_low_confidence_fields,
                "top_match": {
                    "vessel_name": top_match.vessel_name,
                    "total_score": top_match.total_score,
                } if top_match else None,
            })
        return result
    finally:
        session.close()


@app.get("/status", response_model=StatusResponse)
async def get_status():
    """System health check — used by the Electron panel status bar."""
    import time
    session: Session = get_session(engine)
    try:
        vessel_count = session.query(CachedVessel).count()
        pending_orders = session.query(InboundOrder).filter(
            InboundOrder.parse_confidence.is_(None)
        ).count()

        signal_client = SignalCacheClient(session)
        last_refresh = signal_client.get_last_refresh()
        cache_age = None
        if last_refresh:
            cache_age = round(
                (datetime.now(timezone.utc) - last_refresh).total_seconds() / 60, 1
            )

        return StatusResponse(
            status="healthy" if vessel_count > 0 else "no_data",
            vessel_count=vessel_count,
            signal_last_refreshed=last_refresh,
            signal_cache_age_minutes=cache_age,
            pending_orders=pending_orders,
            uptime_seconds=time.time(),
        )
    finally:
        session.close()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket connection for real-time push to Electron panel.
    Panel connects once on startup and receives match results as they arrive.
    """
    await websocket.accept()
    active_websockets.append(websocket)
    log.info("websocket.connected", total=len(active_websockets))
    try:
        while True:
            # Keep connection alive — client sends pings
            await websocket.receive_text()
    except WebSocketDisconnect:
        active_websockets.remove(websocket)
        log.info("websocket.disconnected", total=len(active_websockets))
