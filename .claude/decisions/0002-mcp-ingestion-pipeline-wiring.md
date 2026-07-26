# ADR 0002: Wiring IMAP MCP into the ingestion pipeline

**Status:** Accepted
**Date:** 2026-07-26
**Deciders:** Architect agent (per CLAUDE.md routing of the open question
from ADR 0001)

## Context

ADR 0001 retired Postfix Milter and adopted IMAP MCP as the sole ingestion
model, but explicitly left one question open (ADR 0001, "Open questions" #1):
does Claude orchestrate the full read → parse → match → respond loop via MCP
tool calls, or does an IMAP-connected process still feed the existing
`/internal/ingest` pipeline? CLAUDE.md routes this question to `architect`.

Verified against current source (not just CLAUDE.md's summary):

- **`middleware/mcp/imap_server.py`** exposes `get_latest_orders`,
  `search_orders`, `get_order`, `watch_inbox` as MCP tools over **stdio**
  (`mcp.run(transport="stdio")`, line 89), registered in
  `~/.claude/claude_desktop_config.json` as a `command`/`args`/`cwd`-launched
  subprocess. This is a host-driven protocol: something must already be
  running a live Claude Code/Desktop session (or an SDK-based agent host) to
  spawn and drive it. It is not something a plain web server process can poll
  without embedding an MCP client and managing a subprocess.
- Mode selection (`imap_server.py:29-34`) is a bare
  `os.environ.get("IMAP_MODE", "mock")` picking between `mock_inbox.py`
  (fixed 5-message list, `MOCK_EMAILS` in `middleware/mcp/mock_inbox.py:21-87`)
  and `imap_client.py` (real IMAP, read-only `INBOX` select,
  `middleware/mcp/imap_client.py:16-21`). Both return the same dict shape:
  `{id, sender, subject, received_at, raw_body}`. In `mock_inbox.py` `id` is
  a fixed string (`"mock-0001"`); in `imap_client.py` `id` is the **IMAP
  UID** (`imap_client.py:58`, `_parse_message`). Neither module marks
  messages as read or otherwise tracks what has already been ingested —
  `get_latest_orders()` returns the same messages on every call.
- **`POST /internal/ingest`** (`middleware/api/app.py:288-303`) takes
  `IngestRequest{sender, subject, raw_body}` (`app.py:70-73`), assigns a new
  `uuid.uuid4()` `order_id`, and enqueues `_process_inbound` as a
  `BackgroundTasks` job. It has no concept of a stable source message
  identity — every call creates a new `inbound_orders` row unconditionally.
- **`_process_inbound`** (`app.py:116-208`) does parse
  (`parser.llm_parser.parse_message(raw_body)`, one-shot Anthropic SDK call)
  → persist `InboundOrder` → `SignalCacheClient(session).get_available_vessels()`
  → `MatchingEngine.rank()` → persist `MatchResult` rows → push `NEW_MATCHES`
  over the app's own `active_websockets` list (`app.py:220-283`, module-level
  state, uses `asyncio.get_event_loop()` from whatever thread the caller runs
  in).
- **`InboundOrder`** (`middleware/database/models.py:21-67`) has no column
  for a source message id — nothing to dedupe against. Confirmed against
  the migration too (`middleware/database/migrations/versions/0001_initial_schema.py`,
  `inbound_orders` table def) — same columns, no message-id column.
- **Matching** (`middleware/matching/engine.py`) is unchanged by anything
  here: `MatchingEngine.rank()` takes `list[VesselSnapshot]` and a
  `ParsedOrder`; `VesselSnapshot`s come only from
  `SignalCacheClient.get_available_vessels()`
  (`middleware/signal_client/client.py:228-287`), which queries
  `cached_vessels` and **never** calls the live Signal Ocean API in the
  request path. That cache is populated only by
  `scheduler/jobs.py::_refresh_signal_cache` (APScheduler, interval =
  `settings.signal_cache_refresh_minutes`, `scheduler/jobs.py:22-60`) calling
  `SignalCacheClient.refresh()`, which uses the `signal-ocean` SDK directly —
  not MCP.
- **`middleware/mcp/signal_server.py`** exposes `get_tonnage_list` and
  friends as MCP tools, backed by `mock_vessels.py` (mock) or a *third*,
  separate implementation, `middleware/mcp/signal_client.py` (live) —
  distinct from `middleware/signal_client/client.py` used by the scheduler.
  Note in passing: `middleware/mcp/signal_client.py:27` reads
  `settings.SIGNAL_OCEAN_API_KEY` (uppercase), but `config.py:29` defines the
  field as `signal_ocean_api_key` (lowercase) — this attribute does not
  exist on the `Settings` object, so `SIGNAL_MODE=live` for the Signal MCP
  server is currently broken. It also imports `from database.session import
  get_session`, and `middleware/database/session.py` does not exist (session
  helpers live in `database/models.py`). Nobody currently calls this MCP
  server's tools from the production pipeline, so this bug has no runtime
  impact today — flagged here only as supporting evidence that
  `get_tonnage_list` is not wired to anything and should not be treated as
  load-bearing.
- **`parse_message(raw_body: str) -> ParsedOrder`**
  (`middleware/parser/llm_parser.py:189`) takes only the raw body string —
  sender/subject are stored separately on `InboundOrder`, not passed into the
  parser. No change needed here for MCP-sourced messages; they arrive in the
  same `{sender, subject, raw_body}` shape.

## Decision

1. **No agentic loop in the production ingestion path.** A new APScheduler
   job inside the FastAPI backend polls the mailbox on a fixed interval, in
   the same pattern already used for the Signal Ocean cache
   (`scheduler/jobs.py::_refresh_signal_cache`). It calls the same
   `mock_inbox.py` / `imap_client.py` module that backs `imap_server.py` —
   as a direct Python function call, not over the MCP stdio protocol.
2. **`/internal/ingest`'s pipeline is reused, not duplicated or bypassed.**
   The poller calls `_process_inbound` (`app.py:116`) directly, in-process
   (deferred import to avoid a circular import with `scheduler/jobs.py`
   already being imported by `app.py`) — not an HTTP round-trip to its own
   `/internal/ingest` endpoint. `IngestRequest` and `_process_inbound` both
   gain an optional `source_message_id: str | None = None` field so
   MCP-sourced calls can carry the IMAP message id through; Milter-era /
   manual callers simply omit it and behavior is unchanged.
3. **A new `source_message_id` column on `inbound_orders` is the dedup
   mechanism**, since `get_latest_orders()` returns the same messages on
   every poll. The poller checks this column before calling
   `_process_inbound`, so re-polling never double-ingests or double-charges
   an LLM parse call.
4. **`get_tonnage_list` / the Signal Ocean MCP server get no role in the
   automated match path.** Matching keeps reading `cached_vessels` exclusively
   via `SignalCacheClient.get_available_vessels()`; the cache-refresh job
   (`scheduler/jobs.py::_refresh_signal_cache`, direct `signal-ocean` SDK
   calls) is untouched. `signal_server.py`'s tools remain available only for
   interactive/agentic use (Agent 4 Debug, Agent 5 Briefing, or a human
   running a Claude Code session against the mailbox/tonnage list) — never
   in the unattended request path.
5. **`imap_server.py` and `signal_server.py` are left exactly as built.**
   They keep serving interactive/agentic callers (a Claude Code or Desktop
   session using `claude_desktop_config.json`) for on-demand mailbox
   inspection, debugging, and briefing — that use case is real and unchanged.
   What changes is that they are no longer the only conceivable path to
   production ingestion; the poller is.

## Options considered

| Option | Description | Outcome |
|---|---|---|
| **A. Agentic loop** | A long-running Claude session (Code/Desktop, or a custom Claude Agent SDK process) polls `watch_inbox`/`get_latest_orders` on a timer and orchestrates parse+match itself via tool calls, replacing `llm_parser.py`'s one-shot call. | Rejected for the production path — see Reasoning. |
| **B. In-process poller → existing pipeline (chosen)** | New APScheduler job in the FastAPI app, reusing `mock_inbox.py`/`imap_client.py` directly, feeding `_process_inbound`. | **Accepted.** |
| **C. Standalone poller service over real MCP stdio** | A separate process embeds an MCP client, spawns/talks to `imap_server.py` over stdio, and POSTs to `/internal/ingest` over HTTP. | Rejected — same outcome as B with two extra failure domains (subprocess lifecycle, HTTP loopback) and no behavioral benefit, since `imap_server.py`'s tools are thin passthroughs to the same modules B imports directly. |
| **D. Bypass `/internal/ingest`, write to Postgres directly** | Poller constructs `InboundOrder` rows itself, skipping the parse/match pipeline function. | Rejected — would fork the ingestion logic in two places; any pipeline change (e.g. adding a field, changing parse retry behavior) would need to be kept in sync by hand. |

## Reasoning

- **Production ingestion has to run unattended, 24/7, inside the process
  that already owns lifecycle/health/restart** (`uvicorn` + FastAPI's
  `lifespan`, `app.py:43-51`). An MCP stdio server is designed to be
  spawned and driven by a host application (a Claude client) — it has no
  independent scheduling of its own and nothing keeps it running if no host
  is attached. Tying the broker-facing product's core loop to "someone has a
  Claude Code session open" is an availability regression versus what's
  already running today.
- **The codebase already has exactly this pattern working**: `scheduler/jobs.py`
  runs `_refresh_signal_cache` on an `IntervalTrigger`, `max_instances=1`,
  `coalesce=True`, plus one immediate run at startup. Option B is the same
  shape applied to IMAP polling — minimal new conceptual surface, one
  scheduler instance, easy to reason about and to test (call the poll
  function directly in `pytest`, as already implied by
  `middleware/tests/test_core.py` being the only test module).
  Option A introduces a second, fundamentally different runtime model
  (agent tool-calling loop) alongside it.
  Option C reintroduces exactly the kind of extra moving part
  (subprocess/daemon babysitting) that ADR 0001 already rejected in Milter,
  just relocated from mail interception to MCP transport.
- **ADR 0001 already decided to keep the existing pipeline** ("Keep the
  existing processing pipeline ... in `middleware/` for now" and
  "`POST /internal/ingest` — may remain as an internal handoff endpoint").
  Option B is the literal execution of that: `/internal/ingest`'s logic
  becomes the single ingestion boundary regardless of *how* a message
  arrived (Milter historically, manual POST for local dev, or IMAP MCP now).
  Calling it in-process rather than over HTTP avoids adding a self-loopback
  network hop and its failure modes for zero benefit — the poller already
  lives inside the same Python process.
- **`llm_parser.py` stays a one-shot call** (per ADR 0001, explicitly left
  "unchanged for now"). Nothing about MCP-sourced messages requires Claude
  to see the mailbox agentically in order to extract cargo fields — the
  parser already works on `raw_body` text regardless of where it came from.
- **`get_tonnage_list` has no reason to sit in the matching path.** The
  cache-based design (`cached_vessels`, refreshed independently of any
  inbound message) exists specifically so match latency is decoupled from
  Signal Ocean's response time (per `signal_client/client.py`'s own
  docstring). Routing matches through an MCP tool call per inbound order
  would reintroduce that coupling for no gain, and (per Context) the live
  mode of that particular MCP server is not currently functional anyway.

## Consequences

### To build (handoff to Agent 3 — Coder)

1. **`middleware/database/models.py`** — add to `InboundOrder`
   (`models.py:21-67`):
   ```
   source_message_id = Column(String(255), unique=True, nullable=True)
   ```
   plus an index (`ix_inbound_orders_source_message_id`) in `__table_args__`.
   Nullable + unique so existing Milter/manual rows (no source id) are
   unaffected and duplicates from any one source are still caught.
2. **New Alembic migration** `middleware/database/migrations/versions/0002_source_message_id.py`
   — `op.add_column("inbound_orders", sa.Column("source_message_id", sa.String(255), unique=True, nullable=True))`
   + `op.create_index(...)`, `down_revision = "0001_initial_schema"`. Also
   update `database/seed.py` if it constructs `InboundOrder` rows directly
   (leave `source_message_id` null for seeded rows — they aren't
   MCP-sourced).
3. **`middleware/api/app.py`**:
   - `IngestRequest` (`app.py:70-73`): add
     `source_message_id: Optional[str] = None`.
   - `_process_inbound` (`app.py:116-121` signature): add
     `source_message_id: Optional[str] = None` parameter; pass through to
     the `InboundOrder(...)` constructor (`app.py:137-166`).
   - `ingest` endpoint (`app.py:288-303`): pass `req.source_message_id`
     through to `background_tasks.add_task(_process_inbound, ...)`.
   - No change to `_push_to_websockets` — MCP-sourced orders push
     `NEW_MATCHES` exactly like any other order.
4. **`middleware/scheduler/jobs.py`** — add a second job alongside
   `_refresh_signal_cache`:
   - `_poll_imap_inbox()`: mode-select `mock_inbox` vs `imap_client` from
     `middleware/mcp/` the same way `imap_server.py:29-34` does (bare
     `os.environ.get("IMAP_MODE", "mock")`; **do not** import via a
     package named `mcp` — that collides with the installed `mcp` SDK
     package already imported in `imap_server.py` as
     `from mcp.server.fastmcp import FastMCP`. Add
     `middleware/mcp/` to `sys.path` directly, or use `importlib` with an
     explicit file path, and import the bare module names `mock_inbox` /
     `imap_client`, exactly as `imap_server.py` itself does).
   - Fetch candidates via `source.get_latest_orders(limit=settings.imap_poll_batch_size)`.
   - Open a session, query existing `InboundOrder.source_message_id` values
     already present for the fetched ids in one query, skip any that
     already exist, and for the rest call (deferred import to dodge the
     `app.py` ⇄ `scheduler/jobs.py` circular import)
     `from api.app import _process_inbound` then
     `_process_inbound(sender, subject, raw_body, uuid.uuid4(), source_message_id=msg["id"])`
     for each new message, oldest-first.
   - Register it in `start_scheduler()` (`jobs.py:42-60`) with its own
     `IntervalTrigger(minutes=settings.imap_poll_interval_minutes)`,
     `id="imap_inbox_poll"`, `max_instances=1`, `coalesce=True`, and one
     immediate call at startup — same shape as the existing Signal job.
5. **`middleware/config.py`** — add under a new `# ── IMAP ingestion ──`
   section (near the Signal Ocean block, `config.py:28-32`):
   `imap_poll_interval_minutes: int = 2`, `imap_poll_batch_size: int = 20`.
   Do **not** move `IMAP_MODE` itself into `Settings` — keep it a bare env
   var read the same way `imap_server.py` reads it, so the MCP server
   remains independently startable without pulling in the full
   pydantic-settings config chain (mirrors why `IMAP_MODE`/`SIGNAL_MODE`
   were designed as plain `os.environ` reads in the first place, per the
   docstrings in `imap_server.py:17-22` and `signal_server.py:15-21`).
6. **`.env.example`** — add `IMAP_POLL_INTERVAL_MINUTES=2` and
   `IMAP_POLL_BATCH_SIZE=20` near the existing `SIGNAL_CACHE_REFRESH_MINUTES`
   entry, for consistency.

### To retire

- Nothing new beyond what ADR 0001 already flagged (Milter). This ADR adds
  no new legacy path.

### Unchanged (explicit scope boundary)

- `middleware/parser/llm_parser.py` — one-shot call, untouched.
- `middleware/matching/engine.py` — scoring/ranking logic and weights.
- `middleware/signal_client/client.py` and `scheduler/jobs.py::_refresh_signal_cache`
  — cache refresh stays on the direct `signal-ocean` SDK, on its own
  interval, fully independent of inbound message volume.
- `middleware/mcp/imap_server.py` and `middleware/mcp/signal_server.py` —
  code unchanged; they keep serving interactive/agentic callers via
  `claude_desktop_config.json`. Their tools are not called by the new
  poller directly (the poller imports the same underlying source modules,
  not the MCP tool wrappers).
- `middleware_frontend/` — no changes; it already receives `NEW_MATCHES`
  over the same WebSocket regardless of ingestion source.
- The `mcp/signal_client.py` bug noted in Context is not fixed by this ADR
  — it's unrelated to ingestion wiring and has no bearing on this decision
  since that code path is never invoked from the automated pipeline.

## Open questions (not decided here)

1. **UID stability on live IMAP.** `imap_client.py` uses the IMAP UID as
   `source_message_id`. UIDs are only guaranteed unique within a mailbox's
   `UIDVALIDITY` epoch; a mailbox rebuild on the broker's mail server could
   in principle reassign UIDs and defeat dedup. Not a blocker for
   `IMAP_MODE=mock` (fixed ids) or initial live rollout, but flag to
   `debug` to test against the real WT3 mailbox before go-live, and
   consider hashing the `Message-ID` header as a more robust key if it
   becomes an issue.
2. **AHK trigger fate** — still open per ADR 0001, unaffected by this
   decision.
3. **Whether `_push_to_websockets`'s `asyncio.get_event_loop()` call
   (`app.py:276-279`) behaves correctly when `_process_inbound` is invoked
   from an APScheduler worker thread** rather than FastAPI's own background
   task threadpool. Both are non-main threads today, so this is not a new
   risk introduced by this ADR, but route to `debug` to verify empirically
   once the poller lands, since it's now a second call site.

## References

- `.claude/decisions/0001-ingestion-imap-mcp.md` — the ADR this one resolves
  open question #1 for.
- `CLAUDE.md` — updated alongside this ADR (Plan vs. reality table, open
  questions).
