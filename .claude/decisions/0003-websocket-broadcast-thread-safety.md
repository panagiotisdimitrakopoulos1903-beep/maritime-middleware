# ADR 0003: Fix cross-thread WebSocket broadcast (`_push_to_websockets`)

**Status:** Proposed
**Date:** 2026-07-26
**Deciders:** Architect agent (design), per a debug pass that empirically
confirmed the bug and routed it here — see `middleware/tests/test_scheduler_jobs.py`,
`TestPushToWebsocketsFromWorkerThread`

## Context

**This bug predates ADR 0002 and is broader than it.** It affects the
`POST /internal/ingest` → `BackgroundTasks` call site that has existed since
the original Milter-era pipeline, exactly as much as it affects the new
`_poll_imap_inbox` → APScheduler call site ADR 0002 added. ADR 0002 flagged
it only as an open question (#3) because it introduced a *second* call site
that made the pre-existing problem easier to notice and test, not because it
introduced the problem. Read this ADR as a standalone production bug, not an
ADR-0002 regression.

Verified against current source (not just the debug pass's summary):

- `_push_to_websockets` (`middleware/api/app.py:227-290`) builds a
  `NEW_MATCHES` payload, then for each socket in the module-level
  `active_websockets: list[WebSocket]` (`app.py:38`) does:
  ```python
  asyncio.run_coroutine_threadsafe(
      ws.send_json(payload),
      asyncio.get_event_loop()   # app.py:285
  )
  ```
  The `asyncio.get_event_loop()` call is evaluated as an argument
  expression *before* `run_coroutine_threadsafe` itself runs. On Python
  3.11.7 (confirmed installed version — `middleware/venv`, and
  `fastapi==0.111.0` / `apscheduler==3.10.4` per `middleware/requirements.txt`),
  calling `asyncio.get_event_loop()` on a thread that never had a loop set
  raises `RuntimeError("There is no current event loop in thread '...'")`
  — it does not silently fall back to creating one. That fallback only ever
  existed on the main thread and is being removed from CPython generally.
- That `RuntimeError` is caught by the per-socket `except Exception:
  dead.append(ws)` (`app.py:287-288`), so it never surfaces. Net effect,
  confirmed by `test_new_matches_broadcast_is_silently_lost_from_worker_thread`
  in `middleware/tests/test_scheduler_jobs.py:335-397`:
  1. The `send_json` coroutine object is constructed but never awaited or
     scheduled on any loop — the `NEW_MATCHES` payload is never delivered.
  2. The live, healthy `WebSocket` is misclassified as dead and evicted from
     `active_websockets` (`app.py:289-290`).
  3. No exception escapes `_process_inbound`
     (`middleware/api/app.py:117-215`) — the DB writes in steps 1-4 of the
     pipeline (parse, persist order, match, persist matches) already
     committed before step 5 (`app.py:202`) runs, so ingestion itself is
     unaffected; only the UI push silently fails.
- `_process_inbound` runs on a worker thread at both of its current call
  sites, neither of which is the main thread FastAPI/uvicorn runs its event
  loop on:
  1. `POST /internal/ingest` (`app.py:295-311`) → Starlette
     `BackgroundTasks.add_task` → `anyio.to_thread.run_sync` (a worker
     thread with no asyncio loop set).
  2. `scheduler/jobs.py::_poll_imap_inbox` (`scheduler/jobs.py:70-132`) →
     APScheduler's default executor, a plain
     `concurrent.futures.ThreadPoolExecutor` (also no loop set).

  `test_get_event_loop_raises_on_thread_with_no_loop`
  (`test_scheduler_jobs.py:309-333`) confirms the underlying
  `asyncio.get_event_loop()` behavior directly; `test_new_matches_broadcast_is_silently_lost_from_worker_thread`
  (`test_scheduler_jobs.py:335-397`) exercises the real `app.py` code
  unmodified from a `ThreadPoolExecutor` worker and confirms all three
  findings above end to end.
- **A second, independent problem**: `active_websockets` list mutation is
  not currently thread-safe across call sites. `websocket_endpoint`
  (`app.py:451-466`) appends on connect and removes on disconnect —
  always on the main thread, inside the coroutine FastAPI runs for that
  connection. `_push_to_websockets` iterates and removes
  (`app.py:281-290`) from whatever worker thread called it. CPython's GIL
  makes individual `list.append`/`list.remove` calls atomic, but the
  *sequences* here are not: iterating `active_websockets` on a worker
  thread while the main thread concurrently appends/removes a connection
  can skip or duplicate entries, and `active_websockets.remove(ws)`
  (`app.py:290`, not exception-guarded) raises `ValueError` if the main
  thread already removed that same socket on disconnect between the
  worker thread's iteration and its eviction pass. The same race exists
  between the two worker-thread call sites themselves if a `/internal/ingest`
  push and an `_poll_imap_inbox` push land at the same moment — both would
  iterate/mutate `active_websockets` concurrently with no lock. This is
  latent today (small window, currently masked by the `except Exception`
  swallowing most symptoms) but must not survive the fix, since the fix
  will make broadcasts actually run to completion instead of failing fast.
- `lifespan` (`app.py:43-51`) already runs inside the main thread's asyncio
  loop (`async def lifespan`, entered by uvicorn before it starts serving),
  making it the natural place to capture a reference to that loop with
  `asyncio.get_running_loop()`.

## Decision

1. **Capture the main event loop once, at startup, in a module-level
   global in `middleware/api/app.py`** — not `app.state`, not a
   per-call lookup. Add `_main_event_loop: Optional[asyncio.AbstractEventLoop] = None`
   next to the existing module-level state (`engine`, `matching_engine`,
   `active_websockets`, `app.py:36-38`), and set it in `lifespan`
   (`app.py:44-46`) via `asyncio.get_running_loop()` before `yield`.
2. **Replace the broken per-socket dispatch loop with a single
   `run_coroutine_threadsafe` call per broadcast**, targeting a new
   coroutine that does *all* the list iteration, sending, and eviction on
   the main loop. Worker threads (or the main thread itself, if
   `_push_to_websockets` is ever called from an async handler directly)
   never touch `active_websockets` directly again — they only ever hand
   one coroutine to the captured loop.
3. **This closes the thread-safety gap on `active_websockets` by
   confinement, not locking.** Once both mutation sites — `websocket_endpoint`'s
   connect/disconnect and the broadcast's own eviction — run exclusively as
   code scheduled on the single main event loop thread, they cannot
   interleave with each other at the Python-object level; asyncio
   coroutines on one loop don't preempt each other mid-statement the way
   OS threads do. No `threading.Lock` needed.
4. **A missed or failed broadcast must never crash or roll back
   `_process_inbound`.** If `_main_event_loop` is `None` (broadcast
   attempted before `lifespan` has run — not expected in practice, since
   `start_scheduler()`'s immediate startup jobs run inside `lifespan` after
   the loop is captured, but defend against it anyway) or
   `_main_event_loop.is_closed()` (app is shutting down), log a warning
   and return without raising. Wrap the `run_coroutine_threadsafe` call
   itself in `try/except RuntimeError` for the narrow race where the loop
   closes between the check and the call, for the same reason. Do not
   `await` or `.result()` the returned future from `_process_inbound`'s
   calling thread — keep the broadcast fire-and-forget so ingestion
   latency never depends on WebSocket I/O or a slow/stuck Electron client.
   Attach a `add_done_callback` to the future purely for logging a failed
   broadcast (e.g. a socket that raised inside the new coroutine) —
   observability only, must not re-raise from the callback.

## Options considered

| Option | Description | Outcome |
|---|---|---|
| **A. Capture main loop at startup + `run_coroutine_threadsafe`, broadcast logic itself runs on the main loop (chosen)** | `lifespan` stores `asyncio.get_running_loop()` in a module global; worker threads schedule one coroutine that owns all `active_websockets` reads/writes. | **Accepted.** |
| **B1. Thread-safe queue drained by a periodic main-loop task** | Worker threads push payloads onto a `queue.Queue`; a coroutine on the main loop polls it on an interval and broadcasts. | Rejected — trades an immediate, event-driven push for polling latency (or, to get immediate delivery without polling, you end up calling `loop.call_soon_threadsafe` to wake the drainer anyway, which is `run_coroutine_threadsafe`'s job already, just reimplemented with an extra queue in between for no behavioral gain). Also adds a persistent background task and a queue as new moving parts, the kind ADR 0002 already argued against introducing when an existing, simpler primitive does the job. |
| **B2. Lazily fetch/capture the loop per call instead of centralizing at startup** | E.g., capture `asyncio.get_running_loop()` inside `websocket_endpoint` on first connection instead of in `lifespan`. | Considered a minor variant of A, not a genuinely different design — a worker thread still cannot call `asyncio.get_running_loop()` itself (no loop on that thread), so *some* main-thread capture point handing the reference to worker threads is unavoidable. Capturing in `lifespan` is strictly earlier and connection-order-independent; `_push_to_websockets` already no-ops when `active_websockets` is empty (`app.py:234-235`), so there's no failure mode from capturing before any client has connected. No reason to prefer the later capture point. |
| **B3. Sync-only broadcast via a different transport (e.g. panel polls a REST endpoint instead of WebSocket push)** | Drop push entirely; Electron panel polls `/orders/latest` on an interval. | Rejected — disproportionate to the actual bug (a wrong-thread loop lookup, not a design flaw in using WebSockets), throws away already-working low-latency push and the frontend's existing reconnect logic (`middleware_frontend/src/lib/api.js`), and worsens perceived responsiveness for a broker-facing panel where "new match just arrived" is the point. |

## Reasoning

- **`run_coroutine_threadsafe` is the correct primitive for exactly this
  shape of problem** — scheduling a coroutine onto a *specific*, already-running
  loop from a different thread — and it is documented as safe to call from
  any thread, including the loop's own thread (it schedules via
  `loop.call_soon_threadsafe` internally and returns a `concurrent.futures.Future`
  without blocking). That property is what makes Option A satisfy all three
  required call contexts uniformly, with no thread-detection branching
  needed anywhere in `_push_to_websockets`:
  - **Worker thread (`BackgroundTasks` / APScheduler)** — the case that's
    broken today. Fixed because the loop reference no longer depends on
    thread-local state; it's the one captured at startup.
  - **Main thread, if a future async handler calls `_push_to_websockets`
    directly** — `run_coroutine_threadsafe(coro, _main_event_loop)` called
    from the same thread `_main_event_loop` is running on still works
    correctly (schedules the coroutine for the next loop iteration rather
    than deadlocking or double-running), so no special-casing is needed if
    this call pattern is ever added later.
- **The existing code already reached for `run_coroutine_threadsafe`** — it
  just fed it a loop reference (`asyncio.get_event_loop()`) that doesn't
  exist on the calling thread. The fix is smaller than the initial bug
  report might suggest: keep the primitive, fix what loop it's pointed at,
  and additionally move the list-mutating parts of the loop body into the
  scheduled coroutine itself so they execute where the connect/disconnect
  handlers already execute.
- **Confinement beats locking here.** A `threading.Lock` around
  `active_websockets` would fix the memory-safety race but not the
  higher-level bug (a worker thread still can't correctly run
  `ws.send_json()`, an async method, synchronously under a lock without an
  event loop). Moving all reads/writes of `active_websockets` onto the main
  loop via one coroutine fixes both problems in one design — worker
  threads submit work, they don't touch shared mutable state directly.
- **Fire-and-forget with a logging-only done-callback matches the stated
  product tradeoff**: a missed push degrades the Electron panel's
  liveness (bad, worth fixing) but must never degrade ingestion
  reliability (worse, non-negotiable — brokers' inbound orders must keep
  parsing/matching/persisting even if every websocket client is gone).
  Today's silent swallowing achieves the "never crash ingestion" half by
  accident while failing the "actually deliver" half entirely; the fix
  keeps the former property intentionally and adds the latter.

## Consequences

### To build (handoff to Agent 3 — Coder; this ADR is design-only, nothing
implemented here)

All changes are confined to `middleware/api/app.py`; no changes to
`middleware/scheduler/jobs.py` are needed — it already just calls
`_process_inbound`, which is untouched by this fix.

1. **`middleware/api/app.py`, top of file** — promote `import asyncio` to a
   module-level import (currently a local `import asyncio` inside
   `_push_to_websockets`, `app.py:278`); remove the now-unused local
   `import json` at `app.py:279` (dead code — `WebSocket.send_json`
   serializes internally, nothing in this function calls `json.dumps`).
2. **`middleware/api/app.py:38`** (next to `active_websockets: list[WebSocket] = []`)
   — add:
   ```python
   _main_event_loop: Optional[asyncio.AbstractEventLoop] = None
   ```
3. **`middleware/api/app.py`, `lifespan` (`app.py:43-51`)** — capture the
   loop right after `log.info("app.starting")`:
   ```python
   global _main_event_loop
   _main_event_loop = asyncio.get_running_loop()
   ```
4. **`middleware/api/app.py`, replace lines 278-290** (the `import
   asyncio`/`import json` + per-socket `run_coroutine_threadsafe`/`get_event_loop`
   loop) with:
   - A new module-level async function, e.g. `_broadcast_new_matches(payload: dict) -> None`,
     that contains exactly the logic currently inline: iterate
     `active_websockets`, `await ws.send_json(payload)` per socket in a
     `try/except`, collect dead sockets, remove them from
     `active_websockets` after the loop. This function must only ever run
     on the main loop (it will, because it's only ever invoked via
     `run_coroutine_threadsafe` targeting `_main_event_loop`, never called
     directly).
   - `_push_to_websockets`'s tail becomes: guard on `_main_event_loop is None or _main_event_loop.is_closed()`
     (log a warning, e.g. `log.warning("websocket.broadcast_skipped", reason=...)`,
     return); otherwise `try: future = asyncio.run_coroutine_threadsafe(_broadcast_new_matches(payload), _main_event_loop)` /
     `except RuntimeError as e: log.warning("websocket.broadcast_schedule_failed", error=str(e)); return`.
     On success, `future.add_done_callback(_log_broadcast_outcome)` — do
     not `.result()` or `await` it.
   - Add `_log_broadcast_outcome(future: concurrent.futures.Future) -> None`:
     if `future.exception()` is not `None`, `log.error("websocket.broadcast_failed", error=...)`.
     Must not raise.
5. **No change to `websocket_endpoint`** (`app.py:451-466`) — it already
   only runs on the main loop; it becomes correctly synchronized with the
   new `_broadcast_new_matches` by virtue of both running there, with no
   code change required on its side.
6. **No change to `_process_inbound`** (`app.py:117-215`) — it already
   calls `_push_to_websockets` as the last step and already has a broad
   `except Exception` around the whole pipeline; that catch-all remains a
   backstop but should no longer ever actually fire because of the push
   step, since the push step now handles its own failure modes internally.

### To retire

Nothing. This is a bug fix inside existing scope, not a path change.

### Unchanged (explicit scope boundary)

- `middleware/scheduler/jobs.py` — both job functions, `start_scheduler`,
  `stop_scheduler` untouched.
- The `NEW_MATCHES` payload shape itself (`app.py:237-276`) — this ADR
  fixes delivery, not content.
- `middleware_frontend/` — no changes; it already expects `NEW_MATCHES`
  over the same WebSocket and already has reconnect logic for the
  eviction case, which should now trigger far less often since healthy
  sockets stop being wrongly evicted.
- `CLAUDE.md` — not updated by this ADR. Per the sequencing already
  established by ADR 0001/0002 (both were written and accepted before
  their corresponding `CLAUDE.md` updates landed alongside or after
  implementation), `CLAUDE.md` gets updated once Coder implements this
  fix, not at design time.

## Test impact (route to Agent 4 — Debug; also design-only here, not
implemented in this ADR)

`middleware/tests/test_scheduler_jobs.py`, `TestPushToWebsocketsFromWorkerThread`:

- **`test_get_event_loop_raises_on_thread_with_no_loop`** — **keep
  as-is, do not invert.** It documents a standard-library fact
  (`asyncio.get_event_loop()` raises on a fresh non-main thread), not
  `app.py` behavior. That fact is exactly *why* the fix exists (it's the
  reason a worker thread can never independently obtain "the" main loop
  and must instead be handed a captured reference) and remains true after
  the fix — the fixed code simply stops calling `asyncio.get_event_loop()`
  anywhere. This test continues to serve as documentation of that
  constraint.
- **`test_new_matches_broadcast_is_silently_lost_from_worker_thread`** —
  **must be inverted**, and needs restructuring, not just a flip of its
  three assertions. As written, it calls `app._push_to_websockets`
  directly without ever running `lifespan`, so `_main_event_loop` would be
  `None` in that test's process — under the fix, that specific test as
  written would newly assert "gracefully skipped, no crash, no eviction,
  no delivery," which is correct-but-incomplete: it wouldn't actually
  prove a message gets delivered, only that failure is now safe. Split it
  into (at minimum):
  1. A **no-loop-yet** test: `app._main_event_loop` left at its default
     (`None`), call `_push_to_websockets` from a worker thread, assert no
     exception, no eviction (`active_websockets` unchanged), no delivery,
     and a warning logged — this replaces the old test's role of proving
     ingestion-side safety.
  2. A **positive delivery** test (new): set `app._main_event_loop` to a
     real running loop (e.g. run an `asyncio` loop on what the test treats
     as "the main thread" — a dedicated thread running `loop.run_forever()`
     works fine here since `run_coroutine_threadsafe` doesn't care which
     thread the target loop lives on, only that it's running), call
     `_push_to_websockets` from a separate `ThreadPoolExecutor` worker
     thread (simulating either call site), then drive/await the returned
     future or otherwise synchronize, and assert the fake websocket's
     `delivered` list actually received the payload and was **not**
     evicted.
  3. A **main-thread call** test (new): call `_push_to_websockets` from
     inside a coroutine running on the same loop that's set as
     `_main_event_loop` (no separate thread at all), proving the fix is
     thread-agnostic per the Reasoning section's claim about
     `run_coroutine_threadsafe` being safe to call from the loop's own
     thread.
  4. A **concurrent multi-thread broadcast** test (new): two or more
     worker threads call `_push_to_websockets` at roughly the same time
     (simulating a `/internal/ingest` push and an `_poll_imap_inbox` push
     landing together) while `active_websockets` also gets a connect/disconnect
     from "main thread" code in between — assert no `ValueError`/`RuntimeError`
     from list mutation, and correct final membership/delivery. This is
     the direct regression test for the confinement fix in Decision #3.
  5. A **stale/closed loop** test (new): set `_main_event_loop` to a loop
     that has since been `.close()`d, call `_push_to_websockets` from a
     worker thread, assert graceful no-op (no exception propagates,
     warning logged, no delivery expected).
- No other tests in `test_scheduler_jobs.py` (`TestPollImapInboxDedup`,
  `TestLoadImapSource`, `TestPollImapInboxEmptyOrFailingInbox`) are
  affected — none of them touch `_push_to_websockets` or `active_websockets`.

## Open questions (not decided here)

1. **Whether `_broadcast_new_matches` should batch multiple pending
   payloads if several orders complete in a very tight window**, rather
   than scheduling one coroutine per order. Not addressed here — current
   ingestion rate (one broker mailbox, `imap_poll_interval_minutes=2`) makes
   this a non-issue in practice; revisit only if push volume grows.
2. **Whether the Electron panel needs a missed-broadcast recovery path**
   (e.g. "catch up" on reconnect by calling `/orders/latest`) independent
   of this fix. Today's frontend already reconnects and could in principle
   already do this; not in scope here since this ADR restores correct
   real-time delivery rather than designing an offline-recovery story.

## References

- `.claude/decisions/0001-ingestion-imap-mcp.md` — established the
  ingestion boundary this pipeline sits behind.
- `.claude/decisions/0002-mcp-ingestion-pipeline-wiring.md` — Open
  questions #3 is what routed this investigation to `architect`; this ADR
  resolves it, while restating (per Context above) that the bug itself
  predates and is broader than that ADR.
- `middleware/tests/test_scheduler_jobs.py`,
  `TestPushToWebsocketsFromWorkerThread` — the empirical reproduction this
  ADR designs a fix against.
