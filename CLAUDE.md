# Maritime Middleware — Project Memory

Broker-facing vessel-matching middleware for shipping/chartering desks. It sits
alongside **Telix WT3** (a proprietary Windows telex/email client used by
maritime brokers), extracts structured cargo data from inbound broker
messages using Claude, ranks available vessels from Signal Ocean against that
cargo, and surfaces the ranked matches in a companion desktop panel.

**Read this file first in every session.** It reflects the actual state of
the code, not just the original pitch. Where the built system diverges from
the originally-stated plan, that is called out explicitly in
[Plan vs. reality](#plan-vs-reality) — do not paper over it.

## Repo layout

```
middleware/              Python backend (FastAPI + Postgres)
  middleware/mcp/        IMAP + Signal Ocean MCP servers (mock/live modes)
middleware_frontend/     Electron + React companion panel (Layer 4)
.claude/agents/          Five-agent system definitions
.claude/decisions/       Architecture decision records
```

## Architecture

### Target (decided 2026-07-12 — see ADR 0001)

```
Layer 1  WT3 (Telix)         Broker's existing Windows email/telex client — untouched
         IMAP mailbox        WT3 messages accessible via standard IMAP
         IMAP MCP server     Claude reads/parses the mailbox via MCP tools
Layer 2  middleware/         FastAPI backend: parse → match → persist → push
Layer 3  PostgreSQL          inbound_orders, cached_vessels, match_results, signal_cache_refreshes
Layer 4  middleware_frontend Electron+React panel, docks to the right of WT3 (companion, not a clone)
```

**Ingestion direction:** abandon Postfix Milter; IMAP MCP is the sole
ingestion path — Claude connects to the WT3 mailbox directly via MCP tools.
The parse → match → persist → push pipeline in `middleware/` stays; only
the ingestion boundary changed.

### MCP servers (`middleware/mcp/`)

Built and confirmed working in mock mode (smoke test passed 2026-07-26):

- **`imap_server.py`** — exposes `get_latest_orders`, `search_orders`,
  `get_order`, `watch_inbox`. Mode controlled by `IMAP_MODE` in `.env`
  (`mock` → `mock_inbox.py`, `live` → `imap_client.py`).
- **`signal_server.py`** — exposes `get_tonnage_list`, `get_vessel`,
  `get_ports`, `get_distances`, `get_vessel_classes`, `refresh_status`.
  Mode controlled by `SIGNAL_MODE` in `.env` (`mock` → `mock_vessels.py`,
  `live` → `signal_client.py`).

Configured in `~/.claude/claude_desktop_config.json` for interactive/agentic
use (briefing, debug, ad-hoc mailbox/tonnage inspection). **Wiring into the
FastAPI pipeline is implemented** (decided and built 2026-07-26):
`.claude/decisions/0002-mcp-ingestion-pipeline-wiring.md`. Production
ingestion is a new APScheduler job (`scheduler/jobs.py::_poll_imap_inbox`)
that calls the same `mock_inbox.py`/`imap_client.py` module directly (not
over MCP stdio), dedupes against a new `source_message_id` column on
`inbound_orders`, and feeds the existing `_process_inbound` pipeline — no
agentic loop in the production path. Tested in
`middleware/tests/test_scheduler_jobs.py`, committed. `get_tonnage_list`/
`signal_server.py` remain interactive-only; the match path still reads only
`cached_vessels`.

A related bug — `_push_to_websockets` silently dropping `NEW_MATCHES`
pushes and wrongly evicting healthy sockets when invoked from a worker
thread (affects both the `/internal/ingest` path and the new poller, and
predates both) — is now fixed per
`.claude/decisions/0003-websocket-broadcast-thread-safety.md`: the main
event loop is captured at FastAPI `lifespan` startup and all broadcasts are
routed through it via `run_coroutine_threadsafe`. Implemented and tested
(`middleware/tests/test_scheduler_jobs.py::TestPushToWebsocketsFromWorkerThread`,
6 tests), committed.

`signal_server.py`'s `SIGNAL_MODE=live` backend (`middleware/mcp/signal_client.py`)
was broken (see ADR 0002's Context) and has since been fixed — correct
settings field name, correct session/engine imports, real `signal-ocean`
SDK method/field names — with 22 tests added
(`middleware/tests/test_signal_client_mcp.py`). Three of those fixes are
unverified judgment calls that block trusting `SIGNAL_MODE=live` in
production; see [Open questions and blockers](#open-questions-and-blockers)
below.

### Legacy code (retiring, do not extend)

```
middleware/milter/  Postfix Milter hook — RETIRING, do not extend
```

**Postfix Milter** (`middleware/milter/hook.py`) was server-side mail
interception via `smtpd_milters`. Not part of the target architecture.
See `.claude/decisions/0001-ingestion-imap-mcp.md`. Use IMAP MCP or seed
data / manual `POST /internal/ingest` for local dev. The `pymilter`
dependency has been removed from `middleware/requirements.txt` (2026-07-26
— it doesn't build in this environment and nothing in the current
architecture calls it); the `middleware/milter/` code itself is untouched
and still not deployed.

### Processing pipeline (`middleware/api/app.py::_process_inbound`)

Runs as a FastAPI `BackgroundTask` after `POST /internal/ingest` returns
`202` immediately, or from the APScheduler `_poll_imap_inbox` job
(`scheduler/jobs.py`, ADR 0002) on each poll interval — both call sites
invoke the same `_process_inbound` function from a worker thread:

1. **Parse** — `parser/llm_parser.py::parse_message()`. Direct call to the
   Anthropic Python SDK (`anthropic.Anthropic`, not an MCP tool call), one
   shot, `claude-sonnet-4-6` by default. System prompt hard-codes maritime
   shorthand (ANT/AMS/RTM, WS, LAYCAN, AFRAMAX/SUEZMAX/VLCC size bands,
   etc). Returns a `ParsedOrder` where every field is `{value, confidence}`.
   Retried 3x with exponential backoff (`tenacity`) on transient failures.
   Ports are normalised via `core/ports.py::normalise_port()` against a
   static alias table immediately after parsing.
2. **Persist order** — row written to `inbound_orders` including raw body,
   parsed fields, per-field confidence JSON, and overall `parse_confidence`
   (mean of six core fields; anything under
   `settings.parser_confidence_threshold` (0.75) is flagged in
   `low_confidence_field_names`).
3. **Match** — `SignalCacheClient.get_available_vessels()` reads **only**
   the local Postgres cache (`cached_vessels`), never the live Signal Ocean
   API in the request path. `MatchingEngine.rank()` scores every candidate
   and returns the top `MATCH_TOP_N` (default 5).
4. **Persist matches** — one `match_results` row per ranked vessel,
   denormalised (vessel snapshot copied in) so history survives cache
   changes.
5. **Push** — `NEW_MATCHES` JSON payload broadcast to every connected
   `/ws` WebSocket client (the Electron panel), via
   `run_coroutine_threadsafe` onto the main event loop captured at
   `lifespan` startup (ADR 0003) — safe to call from either worker-thread
   call site above.

### Matching engine (`middleware/matching/engine.py`)

Weighted sum of four 0–1 sub-scores, weights configurable via `.env` and
asserted to sum to 1.0 at `MatchingEngine.__init__`:

| Sub-score | Weight | Function | Logic |
|---|---|---|---|
| Vessel size | 0.35 | `vessel_size_score` | Best at DWT/cargo-qty ratio 1.05–1.35 (ballast/stores headroom) |
| Geography | 0.30 | `geography_score` (`core/ports.py`) | Static area-proximity matrix (ARA, Black Sea, Med, MEG, Far East, US Gulf, West Africa, Baltic, Continent) |
| Date overlap | 0.20 | `date_overlap_score` | Best when vessel opens 0–7 days before laycan start |
| Cargo type | 0.15 | `cargo_type_score` | Static cargo→compatible-vessel-class table (`CARGO_VESSEL_COMPATIBILITY`) |

Unknown/missing inputs score neutral (0.5), not zero — avoids unfairly
penalising vessels/orders with gaps. `database/seed.py` embeds a
**second, hand-duplicated copy** of this scoring logic (`_size_score`,
`_geo_score`, etc.) so the seed script can run standalone without importing
the full app stack — if you change scoring weights or logic in
`matching/engine.py`, check whether `database/seed.py` needs the same edit
or seeded data will silently disagree with the live engine.

### Signal Ocean integration (`middleware/signal_client/client.py`)

Not an MCP server — a direct wrapper around the `signal-ocean` Python SDK
(`TonnageListAPI`). `SignalCacheClient.refresh()` is called only by
`scheduler/jobs.py` (APScheduler `BackgroundScheduler`, interval =
`SIGNAL_CACHE_REFRESH_MINUTES`, default 5 min, `max_instances=1`,
`coalesce=True`), and once immediately on app startup. It iterates a fixed
list of vessel classes (Aframax, Suezmax, VLCC, Panamax, Capesize, Handymax,
Handysize), upserts into `cached_vessels` by `vessel_id` (IMO), and prunes
rows that no longer appear in Signal's response (assumed fixed/gone).
`get_available_vessels()` — used by the matching engine — reads only this
cache and never touches the live API, so matching latency is independent of
Signal Ocean's response time.

### Frontend (`middleware_frontend/`)

Electron + React, **not a WT3 clone** — a frameless, always-on-top, 420px
panel docked to the right edge of the screen (`electron/main.js`), meant to
sit beside WT3, not replace it. `contextIsolation: true`,
`nodeIntegration: false`; renderer only touches Node/OS via
`electron/preload.js`'s `window.electronAPI` (get backend URL, open
external link, minimize, drag — since the frameless window has no native
title bar to drag from). React root (`src/App.jsx`) owns WebSocket
connection + selected-order state; `src/lib/api.js` is the only place that
talks to the backend (REST via `fetch`, push via `WebSocket` with
auto-reconnect after 3s). Backend URL is hardcoded to
`http://127.0.0.1:5000` in both `electron/main.js` and `src/lib/api.js` —
not yet read from a shared config.

### Database (`middleware/database/`)

SQLAlchemy 2.0 models (`models.py`) + Alembic migrations (`migrations/`).
`database/cli.py` (`python -m database.cli {init,migrate,rollback,seed,reset,status}`)
is the single entry point for all DB ops. `init` creates tables directly
from models (bypasses Alembic — used for first run / dev); `migrate` is the
Alembic path. Both exist; be deliberate about which one owns schema state in
a given environment. `database/seed.py` populates realistic
vessels/orders/matches so frontend work doesn't require live API keys.

### Config (`middleware/config.py`)

Single `pydantic-settings` `Settings` object loaded from `.env`
(`.env.example` has the full list with comments). `anthropic_api_key` and
`signal_ocean_api_key` are required (no default) — app will not start
without both. Scoring weights and `parser_confidence_threshold` are tunable
without code changes.

## Plan vs. reality

**Ingestion direction is now decided** (2026-07-12):
`.claude/decisions/0001-ingestion-imap-mcp.md`. Abandon Milter; adopt IMAP
MCP so Claude reads the WT3 mailbox.

| Area | Original pitch | Was built | Target going forward |
|---|---|---|---|
| Ingestion | IMAP MCP server, Claude reads inbox | Milter → REST `/internal/ingest` (legacy, retiring) | **IMAP MCP** — built in `middleware/mcp/`, mock mode confirmed |
| Signal Ocean | Signal Ocean MCP server | Direct `signal-ocean` SDK, Postgres cache + `signal_server.py` MCP | MCP server built; backend cache path unchanged for now |
| UI | Full WT3 UI clone | Companion Electron panel beside WT3 | Unchanged — companion panel, not a clone |
| Claude's role | Agentic loop with MCP tool access | One-shot SDK call in `llm_parser.py` | **Implemented** (ADR 0002, decided and built 2026-07-26) — one-shot `llm_parser.py` stays; MCP is interactive-only, not the production ingestion loop |
| Five-agent system | Five specialized agents | `.claude/agents/` (5 agents) | In progress |

See [Open questions and blockers](#open-questions-and-blockers) below for
what's still unresolved across ADRs 0001–0003.

## Open questions and blockers

Consolidated here so they're discoverable without re-reading every ADR.
Route resolution to `architect` unless noted otherwise.

**Still open:**

| Question | Source | Notes |
|---|---|---|
| IMAP UID stability as the `source_message_id` dedup key on a real (non-mock) mailbox — a `UIDVALIDITY` reset could reassign UIDs and defeat dedup | ADR 0002, Open questions #1 | Not a blocker for `IMAP_MODE=mock` or initial live rollout; flag to `debug` to test against the real WT3 mailbox before go-live. Consider hashing `Message-ID` if it becomes an issue |
| Whether `_broadcast_new_matches` should batch multiple pending payloads if several orders complete in a tight window | ADR 0003, Open questions #1 | Low priority — current single-mailbox poll rate makes this a non-issue in practice |
| Whether the Electron panel needs a missed-broadcast recovery path (e.g. catch up via `/orders/latest` on reconnect) | ADR 0003, Open questions #2 | Low priority — not in scope of the ADR 0003 fix, which restores real-time delivery only |

**Blocking `SIGNAL_MODE=live` production trust** — no real Signal Ocean API
key has been available to verify any of these three judgment calls made
while fixing `middleware/mcp/signal_client.py`; see
`middleware/tests/test_signal_client_mcp.py`'s header comment for full
reasoning:

- `refresh()`'s `loading_port` argument to `get_tonnage_list()` uses an
  arbitrary anchor port resolved from `get_ports()` (the wrapper's own
  signature has no caller-supplied port) — whether this yields
  correct/complete tonnage list results from the real API is unverified.
- `get_distances()` defaults `loading_condition_id` to `BALLAST` (reasoning:
  a vessel's open position implies sailing to load empty) — a design guess,
  not verified against real API output or domain expertise.
- `get_distances()` hardcodes vessel_class to `"Aframax"` as a stand-in,
  since the wrapper's own signature has no vessel-class parameter
  (reasoning: distance is geography-dominated, not class-dominated) — also
  unverified.

**Known failing test, pre-existing and unrelated to today's work:**
`middleware/tests/test_core.py::TestDateOverlapScore::test_vessel_opens_day_of_laycan`
has been failing throughout, in `matching/engine.py`'s date-overlap scoring
logic. Confirmed present before, and untouched by, ADR 0002/0003 and the
`signal_client.py` fixes. Not yet investigated — needs a `debug` pass.

**Resolved:** ADR 0002's Open questions #3 (whether `_push_to_websockets`'s
`asyncio.get_event_loop()` call behaves correctly when `_process_inbound`
runs on an APScheduler worker thread) is resolved by ADR 0003 — see the MCP
servers section above. Not carried forward as open.

**Resolved (2026-07-26):** AHK trigger's long-term fate (ADR 0001, Open
questions #2; restated ADR 0002, Open questions #2) — retired outright.
IMAP MCP is production-ready (ADR 0002, fully implemented and tested), so
the AHK fallback is no longer needed; it's dead code in the same category
as `middleware/milter/hook.py`. The file
(`middleware_frontend/trigger/wt3_watcher.ahk`) has been deleted. IMAP MCP
is the sole ingestion path per ADR 0001; no client-side fallback remains.

## Five-agent system

Defined in `.claude/agents/`. Roles: `ceo.md` (coordination, project memory
owner — keeps this file and `.claude/decisions/` current), `architect.md`
(system design, owns the plan-vs-reality reconciliation above),
`coder.md` (backend Python/Node implementation), `debug.md` (testing,
error handling, `pytest tests/`), `briefing.md` (daily summary of inbox
orders + match activity, reads from `/orders/latest` and `/status`).

## Conventions worth knowing

- Logging is `structlog` everywhere in the Python backend — keep using
  structured `log.info("event.name", key=value)` calls, not f-strings.
- All money/quantity fields carry a `confidence` alongside `value`
  (`FieldWithConfidence` in `llm_parser.py`) — don't add a field to
  `ParsedOrder` without both.
- `MatchResult` rows denormalise vessel data at match time on purpose (for
  audit history as the cache changes) — don't "normalize" this into a
  foreign-key-only relationship.
- Tests now span three files: `middleware/tests/test_core.py` (matching
  engine/scoring), `test_scheduler_jobs.py` (IMAP poll dedup and the
  cross-thread websocket broadcast fix, ADR 0002/0003), and
  `test_signal_client_mcp.py` (Signal Ocean MCP `SIGNAL_MODE=live` field
  mapping, 22 tests). Parser LLM calls (`llm_parser.py`) and the legacy
  Milter hook still have no automated coverage.
- `middleware/milter/` is **retiring** per ADR 0001 — do not extend it;
  new ingestion work goes toward IMAP MCP.

## Running it locally

```bash
# Backend
cd middleware
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in ANTHROPIC_API_KEY, SIGNAL_OCEAN_API_KEY
python -m database.cli init
python -m database.cli seed   # realistic fake data, no API keys needed for this part
uvicorn main:app --host 127.0.0.1 --port 5000 --reload

# Frontend (separate terminal)
cd middleware_frontend
npm install
npm start   # React dev server + Electron together
```

Milter is **retiring** — do not run `python -m milter.hook` for new work.
Local dev uses IMAP MCP (mock mode), seed data, or a manual
`POST /internal/ingest`.
