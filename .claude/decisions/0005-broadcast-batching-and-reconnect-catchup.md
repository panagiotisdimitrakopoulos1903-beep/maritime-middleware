# ADR 0005: Broadcast Coalescing and Reconnect Recovery for the Electron Panel Push Path

**Status:** Accepted
**Date:** 2026-07-29
**Deciders:** Architect agent (design), resolving ADR 0003's Open questions
#1 and #2

## Context

ADR 0003 fixed cross-thread WebSocket broadcast delivery
(`_push_to_websockets` / `_broadcast_new_matches`,
`middleware/api/app.py`) but explicitly deferred two related-but-distinct
questions rather than deciding them:

1. "Whether `_broadcast_new_matches` should batch multiple pending
   payloads if several orders complete in a very tight window, rather
   than scheduling one coroutine per order." (ADR 0003, Open questions #1)
2. "Whether the Electron panel needs a missed-broadcast recovery path
   (e.g. 'catch up' on reconnect by calling `/orders/latest`)." (ADR 0003,
   Open questions #2)

Both were reasonable to defer at the time — ADR 0003's job was to make a
single broadcast actually arrive; whether *multiple* broadcasts should be
merged, and whether a *disconnected* client needs an active recovery
protocol, are different problems layered on top of that fix. This ADR
decides both, using everything built since (the full ADR 0004 scope: SMTP
send, RFC 2822 threading, Inbox/Sent folders, read/unread state) plus a
closer look at frontend behavior neither prior ADR examined in detail.

**Verified against current source:**

- `_process_inbound` (`middleware/api/app.py:192-304`) calls
  `_push_to_websockets` exactly once per completed order, as its last step
  (`app.py:291`). `_push_to_websockets` (`app.py:316-379`) builds one
  `NEW_MATCHES` payload for that order and schedules one
  `_broadcast_new_matches` coroutine via
  `asyncio.run_coroutine_threadsafe(..., _main_event_loop)`
  (`app.py:372-374`); that coroutine (`app.py:382-399`) iterates
  `active_websockets` and sends the payload to every connected socket.
  Confirmed: there is no coalescing across orders today — N orders
  completing anywhere near each other means N independent scheduled
  coroutines, each doing its own full iteration/send/evict pass over
  `active_websockets`.
- `_process_inbound` has exactly two call sites:
  1. `POST /internal/ingest` → `BackgroundTasks.add_task` — per ADR 0001,
     IMAP MCP is the sole *production* ingestion path; this HTTP endpoint
     is now the manual/dev/legacy trigger (seed data, ad-hoc testing), not
     live broker traffic. Concurrent calls here reflect manual testing
     cadence, not production volume.
  2. `scheduler/jobs.py::_poll_imap_inbox` (`scheduler/jobs.py:70-141`) —
     the real production path. Its `for msg in new_messages:` loop
     (`scheduler/jobs.py:115-131`) calls `_process_inbound` **sequentially,
     on one worker thread, within one job execution** — not concurrently.
     `settings.imap_poll_batch_size` defaults to 20
     (`middleware/config.py:39`) and `imap_poll_interval_minutes` defaults
     to 2 (`config.py:38`), so a mailbox backlog of up to 20 messages can
     in principle be processed in a single poll cycle.
  3. Critically, each loop iteration's `_process_inbound` call includes a
     real one-shot Anthropic API call (`parser/llm_parser.py::parse_message`,
     per CLAUDE.md) before that order's push fires. This is real network +
     inference latency (at minimum hundreds of milliseconds, realistically
     seconds), not a no-op — it gates every iteration of the loop. A batch
     of 20 backlogged messages is therefore delivered as a trickle spaced
     by parse latency over tens of seconds, not a genuine sub-second burst.
- Frontend, verified against current source:
  - `middleware_frontend/src/lib/api.js::connectWebSocket()`
    (`api.js:135-169`) auto-reconnects 3s after `onclose`
    (`api.js:160-163`, using `setTimeout`) and clears the retry via
    `clearInterval(reconnectTimer)` in `onopen` (`api.js:144`) — a
    mismatched `setTimeout`/`clearInterval` pair. This is a pre-existing,
    previously-flagged, out-of-scope latent bug (works today because the
    JS engines in play share one timer-id namespace between
    `setTimeout`/`setInterval`); not fixed here, noted only because it's
    adjacent to reconnect behavior (see Open questions).
  - `middleware_frontend/src/components/OrderList.jsx` fetches
    `api.getLatestOrders(40)` unconditionally on mount and every 20s
    thereafter (`OrderList.jsx:100-117`), **independent of WebSocket
    connection state** — this loop does not check `wsConnected` or pause
    when the socket is down. It also refetches on every `newOrderIds`
    change (`OrderList.jsx:120-122`), i.e. once per received push.
  - `middleware_frontend/src/App.jsx`'s `subscribeToMatches` callback
    (`App.jsx:53-77`) is what's actually reconnect-fragile: it populates
    `newOrderIds` (pulse-badge highlight, auto-clears after 8s,
    `App.jsx:57-61,73-76`) and auto-selects an order if none is currently
    selected (`App.jsx:64-70`) — both driven **only** by the live
    `NEW_MATCHES` push event firing, with no fallback tied to
    `OrderList`'s independent poll.
  - **Loopback topology**: `middleware_frontend/electron/main.js:11`
    defaults `BACKEND_URL` to `process.env.BACKEND_URL || "http://127.0.0.1:5000"`
    (made configurable via env var in a prior fix; no longer hardcoded, but
    still localhost by default); `api.js`'s `FALLBACK_BASE` (`api.js:11`)
    mirrors the same default/override pattern. In the deployment that
    exists today, the Electron panel and the FastAPI backend it talks to
    run on the same machine. WebSocket
    disconnects in this topology are not "broker's network flaked" events
    — they correlate with local process-level events (backend restart,
    e.g. `uvicorn --reload` picking up a code change; backend crash/relaunch;
    OS-level hiccup), most of which also pause `_process_inbound` itself
    (the backend process isn't running to ingest while it's restarting).

## Decision

**Both open questions are resolved as "no code change" — for different,
independently-sufficient reasons — with an explicit revisit trigger for
each, not a silent re-defer.**

1. **No batching/coalescing is added to `_broadcast_new_matches` /
   `_push_to_websockets`.** Per-order dispatch (as fixed by ADR 0003)
   stays exactly as built. Reasoning below; summary: the "very tight
   window" premise the question was framed around does not actually occur
   in this codebase's real call pattern — the one production call site
   processes messages sequentially and each iteration is naturally
   throttled by a real LLM API call before its push fires.
2. **No reconnect-catchup mechanism (e.g. an `/orders/latest` call
   triggered specifically on `ws.onopen`, or a timestamp-diffing protocol
   to retroactively synthesize `newOrderIds`/auto-select for orders missed
   during a disconnect) is added to the frontend.** `OrderList.jsx`'s
   existing unconditional 20s poll already guarantees any order that
   completed processing during a disconnect becomes visible within at
   most one poll interval, regardless of WebSocket state — this already
   satisfies the data-availability concern the open question was about.
   What remains unrecovered — the pulse-badge highlight and auto-select
   convenience for orders that arrive during a disconnect — is accepted as
   a cosmetic, bounded, self-healing gap (the next live push, or simply
   opening the order from the list, fully restores normal behavior for
   that order).

Neither decision is a re-deferral: both were evaluated against concrete
current behavior (not hypothetical future scale) and found to already be
adequately handled by existing mechanisms, consistent with ADR 0003's
practice of stating plainly when something is a non-issue rather than
building for it preemptively.

## Options considered

### Batching / coalescing pending broadcasts

| Option | Description | Outcome |
|---|---|---|
| **A. No change — keep per-order dispatch (chosen)** | Leave `_push_to_websockets`/`_broadcast_new_matches` exactly as ADR 0003 built them; one coroutine per completed order. | **Accepted.** Matches the actual call pattern (sequential, LLM-latency-throttled); nothing to coalesce in practice. |
| **B. Debounce buffer: accumulate payloads for a short window (e.g. `asyncio.sleep(N)`), flush as one `NEW_MATCHES_BATCH` message** | A module-level pending list plus a short-lived (not persistent) flush task scheduled on first arrival, draining and clearing on timeout. Technically avoids reintroducing ADR 0003's Option B1 (a *permanently*-running periodic drain task) since this task would only exist transiently while payloads are pending. | Rejected. Solves a problem that doesn't currently occur (see Context — pushes are naturally spaced by parse latency in the only production call site) and introduces a new failure surface symmetric to what ADR 0003 fixed: a buffer that must itself be flushed correctly on shutdown, whose loss-of-payload-on-crash characteristics would need the same defensive analysis ADR 0003 gave `_main_event_loop` (closed loop, no loop yet, etc.) for no corresponding benefit today. Revisit only if the revisit trigger below fires. |
| **C. Move batching responsibility into `_poll_imap_inbox`'s loop itself** (collect ranked results across iterations, single push after the loop) | Would require splitting `_process_inbound` into "process" and "process + push" variants so its two call sites (single-order HTTP ingest vs. multi-order poll) could opt in/out of per-order pushing. | Rejected. Adds a call-site-dependent branch to a function both prior ADRs kept as a single uniform pipeline; the added coupling/complexity is disproportionate to a problem that, per the Context section, does not manifest as a real burst given sequential + LLM-latency-gated processing. |

### Reconnect / missed-broadcast recovery

| Option | Description | Outcome |
|---|---|---|
| **A. No change — rely on `OrderList.jsx`'s existing unconditional 20s poll (chosen)** | Leave `connectWebSocket()`, `OrderList.jsx`, and `App.jsx` exactly as built. | **Accepted.** The poll already delivers any missed order within ≤20s regardless of WS state; the loopback topology (same-machine WS) further narrows how often a "backend keeps ingesting, panel is disconnected" window can even occur. |
| **B. `ws.onopen`-triggered catch-up**: on reconnect, call `api.getLatestOrders()`, diff against previously-known order ids (e.g. against "last known connected timestamp"), and synthesize `newOrderIds` pulses / auto-select for anything new | Would require plumbing a "last seen" timestamp or id set between `App.jsx` (owns `newOrderIds`/`selectedOrderId`) and `OrderList.jsx` (owns fetching), since currently neither component tracks disconnect duration or order arrival order across the two. | Rejected. Disproportionate: solves a cosmetic affordance gap (missed pulse/auto-select for orders that already appear in the list within 20s) by adding cross-component state plumbing that doesn't exist today, for a gap with no reported broker impact. |
| **C. Backend replays missed broadcasts on reconnect** (server tracks per-socket delivery state, replays payloads sent while that socket was disconnected) | Symmetric server-side alternative to Option B. | Rejected, more strongly than B. Requires the backend to track per-connection delivery history — new persistent state exactly analogous to ADR 0003's rejected Option B1 (a queue/backlog abstraction), for a problem the frontend's existing poll already solves at the data layer. Violates ADR 0003's asymmetry principle in the wrong direction: it would make the push path *more* stateful to protect a nice-to-have, when the principle is the opposite (durable data path stays simple and authoritative; push is a thin, droppable convenience layer on top). |

## Reasoning

- **The "tight window" premise doesn't hold for the one call site that
  matters.** ADR 0003's question #1 was framed hypothetically ("if several
  orders complete in a very tight window"). Tracing the actual code: the
  only production ingestion path (`_poll_imap_inbox`) processes its batch
  of up to 20 messages **sequentially on a single worker thread**, and
  every iteration is gated by a real Anthropic API call
  (`parse_message`). That call is the dominant cost of each iteration and
  is not near-zero — it's real network I/O plus LLM inference. So even a
  full 20-message backlog arrives at the WebSocket as a trickle over tens
  of seconds, not a burst. The manual/dev `POST /internal/ingest` path
  could in principle be hit concurrently, but per ADR 0001 it's not a
  production traffic pattern, so designing broadcast infrastructure around
  it would be optimizing for a test harness, not the product.
- **Extra frames, if they ever did arrive close together, are not a
  correctness bug — only a mild efficiency one, and even that is bounded.**
  Each `NEW_MATCHES` payload is self-contained (one order + its up to
  `MATCH_TOP_N=5` ranked matches). N payloads instead of 1 combined payload
  means N small JSON frames to (typically) a single connected socket, and
  N redundant `fetchOrders()` calls in `OrderList.jsx` (`OrderList.jsx:121`,
  which refetches on every `newOrderIds` change) — wasted work, not wrong
  state, since each fetch is idempotent and the last one to resolve wins.
  This mirrors ADR 0003's own distinction between "actually broken" and
  "latent, currently masked, must not get worse" — here it's the weaker
  case: correct today, and not on a path to becoming incorrect either.
- **ADR 0003's Option B1 rejection reasoning does transfer to the batching
  question, even though it's a distinct problem.** B1 rejected a
  queue-drained-by-periodic-task design for reliable *delivery* of a single
  broadcast, on the grounds that it adds a persistent moving part where a
  direct primitive (`run_coroutine_threadsafe`) already does the job. A
  debounce buffer for *coalescing multiple* broadcasts is a different
  problem, but would introduce the same category of new moving part (state
  that must be correctly flushed, defended against loop-closed/shutdown
  races, etc.) for a payoff that doesn't exist yet given the sequential,
  latency-gated call pattern above. Consistent with ADR 0003's principle
  of preferring existing simple primitives over new persistent
  machinery, the answer here is: don't add the buffer until there's an
  actual burst to coalesce.
- **The data-loss half of the reconnect question is already answered by
  code that predates this ADR** — `OrderList.jsx`'s 20s poll
  (`OrderList.jsx:113-117`) was written to run regardless of WebSocket
  state; ADR 0003's authors apparently didn't factor this in when
  deferring the question (its Open questions #2 note says "today's
  frontend already reconnects and could in principle already do this" —
  true, and it turns out the polling half of "reconnect and catch up"
  already exists independently of reconnect, on a fixed timer instead).
  Re-deriving a reconnect-triggered version of the same fetch would be
  redundant with a poll that already fires every 20s.
- **The loopback topology changes the risk calculus for what's left (the
  pulse-badge/auto-select gap).** This isn't a broker-on-a-ship's-satellite-link
  scenario; both ends of the WebSocket are the same machine. Disconnects
  correlate with the backend process itself restarting or hiccupping —
  during which `_process_inbound` also isn't running, so there's no
  window where "orders are actively completing" and "the panel is
  disconnected" overlap for long. The residual gap is real but narrow:
  a disconnect caused by something *other* than backend downtime (e.g. a
  transient local socket/OS issue while the backend keeps serving) could
  still cause a missed pulse/auto-select for an order that then just
  quietly appears in the list on the next 20s poll. That is exactly the
  tier of thing ADR 0003's stated asymmetry ("pushes/UI-liveness are a
  nice-to-have layered on top of already-durably-persisted data") accepts
  as tolerable degradation, not something requiring a recovery protocol.
- **Building either mechanism now would add state with no current bug to
  justify it.** Both rejected designs (a payload buffer; a reconnect-diff
  protocol) are the kind of infrastructure that's easy to add and hard to
  reason about later once broadcast volume or disconnect frequency
  actually changes for reasons unrelated to today's architecture (e.g. a
  second broker mailbox, a much larger `imap_poll_batch_size`, a faster
  parser removing the natural throttle). Declining to build them now,
  with an explicit revisit trigger recorded below, follows ADR 0003's
  practice more faithfully than building speculative machinery would.

## Consequences

### To build (handoff to Agent 3 — Coder)

**Nothing.** This ADR authorizes zero code changes. Both open questions
resolve to "existing behavior is already correct for current scale and
topology," not to a design awaiting implementation. This is a deliberate,
reasoned outcome — the same category of ADR resolution CLAUDE.md already
records for the date-overlap scoring investigation ("test itself had the
incorrect expected value... engine was already correct") — not a
non-decision.

### To retire

Nothing. No code path is being deprecated or replaced.

### Unchanged (explicit scope boundary)

- `middleware/api/app.py::_process_inbound` (`app.py:192-304`),
  `_push_to_websockets` (`app.py:316-379`), `_broadcast_new_matches`
  (`app.py:382-399`) — exactly as ADR 0003 left them. Per-order dispatch,
  fire-and-forget scheduling, `_main_event_loop`/closed-loop guards all
  stand.
- `middleware/scheduler/jobs.py::_poll_imap_inbox` (`scheduler/jobs.py:70-141`)
  — sequential per-message processing within one poll cycle, unchanged.
- `middleware/config.py`'s `imap_poll_interval_minutes` (2) and
  `imap_poll_batch_size` (20) defaults — unchanged; not being tuned as
  part of this ADR.
- `middleware_frontend/src/lib/api.js::connectWebSocket()`
  (`api.js:135-169`) — 3s reconnect delay, and the pre-existing
  `clearInterval`/`setTimeout` mismatch (`api.js:144` vs. `api.js:162`),
  both unchanged. The mismatch remains flagged as a known, out-of-scope,
  currently-harmless latent bug (see Open questions).
- `middleware_frontend/src/components/OrderList.jsx` — unconditional 20s
  poll (`OrderList.jsx:113-117`) and the per-push refetch
  (`OrderList.jsx:120-122`), both unchanged.
- `middleware_frontend/src/App.jsx`'s `subscribeToMatches` handler
  (`App.jsx:53-77`) — `newOrderIds` pulse-badge and auto-select logic,
  unchanged; still driven only by live push events, with the accepted gap
  described in Decision #2.
- `CLAUDE.md` — not updated by this ADR. Per the sequencing established by
  ADR 0001-0004 (each written and accepted before its corresponding
  CLAUDE.md update), and doubly so here since there is no implementation
  for CLAUDE.md to eventually describe — this ADR's outcome is "current
  CLAUDE.md text about these two open questions is superseded by this
  ADR's resolution," which `ceo` can fold in as a documentation-only edit
  whenever convenient, not gated on `coder` output.

## Open questions (not decided here)

1. **Revisit trigger for the batching decision**: if any of the following
   change, the "no batching needed" conclusion should be re-examined, not
   assumed to still hold — (a) a second broker mailbox or additional
   ingestion source is added such that `_poll_imap_inbox` (or an
   equivalent) genuinely dispatches concurrently rather than sequentially;
   (b) `imap_poll_batch_size` is raised well beyond 20 as a matter of
   normal operation rather than worst-case headroom; (c) `parse_message`'s
   latency drops dramatically (e.g. a cheaper/faster model, response
   caching, or batching the LLM call itself) such that the natural
   per-message throttle this ADR's reasoning leans on no longer holds.
2. **Revisit trigger for the reconnect decision**: if brokers report
   noticeably stale data in practice (i.e. the 20s poll interval itself
   feels slow, independent of WebSocket state), that's a reason to shorten
   the poll interval — a small, local change to `OrderList.jsx` — not
   necessarily a reason to build the reconnect-diff protocol rejected
   above. The two are separable: poll cadence is a tuning knob, reconnect
   recovery is a design the Options table above still holds is
   disproportionate.
3. **The `clearInterval`/`setTimeout` mismatch in `connectWebSocket()`**
   (`api.js:144` vs. `api.js:162`) remains explicitly out of scope, as
   flagged previously. It's noted here only because it's adjacent to
   reconnect behavior: if a future ADR ever *does* decide to build a
   reconnect-triggered mechanism (contrary to Decision #2 above), that
   design would need a reliable "we just reconnected" signal, and fixing
   this mismatch (using the matching `clearTimeout`/`setTimeout` pair)
   would become a real prerequisite at that point, not an optional
   cleanup. Not a reason to fix it now, since Decision #2 doesn't build
   that mechanism.

## References

- `.claude/decisions/0001-ingestion-imap-mcp.md` — established IMAP MCP as
  the sole production ingestion path, which is why `POST /internal/ingest`
  concurrency is treated here as a dev/test concern, not a production
  batching concern.
- `.claude/decisions/0002-mcp-ingestion-pipeline-wiring.md` — introduced
  the `_poll_imap_inbox` call site and `imap_poll_batch_size`, whose
  sequential-processing behavior this ADR relies on.
- `.claude/decisions/0003-websocket-broadcast-thread-safety.md` — fixed
  broadcast delivery itself and originated both open questions this ADR
  resolves; its Option B1 rejection and its "push is a nice-to-have layered
  on durable data" asymmetry principle are both carried forward here.
- `.claude/decisions/0004-smtp-send-and-wt3-clone-email-client.md` — the
  full scope of work built since ADR 0003 (SMTP send, threading,
  Inbox/Sent, read/unread); confirmed here as not changing ingestion
  *volume*, only what happens to a message after ingestion, which is why
  it doesn't change either conclusion in this ADR.
- `middleware/tests/test_scheduler_jobs.py`,
  `TestPushToWebsocketsFromWorkerThread` — the existing test suite this
  ADR's "no change" decisions leave untouched; no new tests are needed
  since no new behavior is being introduced.
