# ADR 0007: Explicit Parse-Failure Status, Match Gating, and Frontend Treatment

**Status:** Accepted
**Date:** 2026-07-30
**Deciders:** Architect agent (design), investigating a UX/data-integrity bug
found while testing the running app under this environment's current
invalid `ANTHROPIC_API_KEY`

## Context

A broker looking at the Electron panel currently cannot tell the difference
between two very different situations that render identically:

- A genuine borderline match — the parser extracted real cargo/date/geo
  data with moderate confidence, and the matching engine scored a vessel
  around 50% using that real data.
- A total parse failure — the LLM call never returned usable structured
  data at all (exhausted retries, malformed response, transient API error,
  or — as currently reproducible in this dev environment — an invalid
  `ANTHROPIC_API_KEY`), yet the matching engine still ran against an
  essentially empty `ParsedOrder`, hit every "unknown/missing input scores
  neutral (0.5)" branch in `matching/engine.py` on every sub-dimension for
  every candidate vessel, and produced a flat "50 SCORE" on all of them.
  That number is not a computed judgment about any vessel — it's the
  fallback default appearing five times in a row and looking exactly like
  a real one.

This is a trust/data-integrity problem: a broker could act on a
fabricated-looking match for an order that actually failed to parse. This
ADR designs the fix. No code is written here — implementation is handed to
`coder`.

### What's already available vs. what's missing (verified directly)

**`parser/llm_parser.py::parse_message()`** already distinguishes these
cases internally, but doesn't expose the distinction as a first-class
field:

- The `try` block (lines 197–236) runs the LLM call, parses JSON, computes
  `parse_confidence`/`low_confidence_field_names` from whatever the LLM
  actually returned — including the case where every field comes back
  `null`/`0.0` confidence because the message was genuinely uninformative.
  This path never sets `order.error`.
- Both `except` blocks (`json.JSONDecodeError` at line 238, generic
  `Exception` at line 243 — which is what actually fires today, since
  `tenacity`'s `@retry` on `_call_llm` re-raises a `RetryError` wrapping
  the underlying `AuthenticationError` after 3 exhausted attempts) set
  `order.error = str(e)` and `order.parse_confidence = 0.0`, and leave
  every `FieldWithConfidence` at its class default (`value=None,
  confidence=0.0`).

So `ParsedOrder.error` (mapped to `InboundOrder.parse_error`, a `Text`
column that already exists on the model) is **already a reliable "parsing
failed outright" signal** — confirmed directly against this environment's
own dev DB, not assumed:

```
total_orders                     10
zero_conf_orders                  5
match_rows_for_zero_conf_orders  25
```

All 5 zero-confidence rows have `parse_error` populated
(`RetryError[<Future ... state=finished raised AuthenticationError>]`) and
all 6 core-field confidence scores exactly `0.0`. There is currently no
row in this DB where `parse_confidence == 0` with `parse_error IS NULL` —
i.e. no observed case (yet) of a structurally successful-but-totally-blank
parse — but the code path for it exists (an LLM response that's valid JSON
with every field `null`/`0.0`) and must be treated as **success**, not
failure: it reflects a real (if very weak) judgment call by the model
about an uninformative message, not an infrastructure failure. Collapsing
that into "failed" would be its own data-integrity error in the other
direction — it would suppress a legitimate 0.5-neutral-scored match that
a broker might reasonably still want to see the vessel candidates for.

**`parse_error IS NOT NULL` is therefore already sufficient signal today**
to derive failed-vs-successful — but it is a `Text` column holding a raw
exception repr, not a stable contract. Two API consumers (the REST
response and the WebSocket push payload) would each need to independently
re-derive `"failed" if parse_error else "success"` logic, and any future
third state (there is a real one worth flagging now, in Open questions)
would require every consumer to change its inference logic simultaneously.
Per this codebase's own established convention of explicit stored status
alongside underlying detail (`OutboundMessage.send_status` +
`error_message`, `SignalCacheRefresh.success` + `error_message`), this ADR
adds a proper `parse_status` field rather than leaving every consumer to
infer state from a text blob.

## Decision

### 1. New explicit `parse_status` field: `"success"` / `"failed"` — two values, not three

`parser/llm_parser.py::ParsedOrder` gains:

```python
parse_status: str = "success"   # "success" | "failed"
```

Set to `"failed"` in both existing `except` blocks (`json.JSONDecodeError`
and generic `Exception`), alongside the existing `order.error = ...` /
`order.parse_confidence = 0.0` assignments already there. Left at its
default `"success"` on the normal `try` path — **regardless of how low
`parse_confidence` ends up**, including the all-null-fields case described
above.

**Two values, not the `success`/`failed`/`partial` three-value enum the
task prompt floated.** No code path in this pipeline currently produces a
state distinct from both: a structurally successful LLM response with weak
per-field confidence is not "partial" in any way the system treats
differently from a confident one — `has_low_confidence_fields` /
`low_confidence_field_names` already carry that gradation, per-field, and
`matching/engine.py`'s neutral-0.5 fallback already exists specifically to
handle missing/low-quality individual fields inside an otherwise-successful
parse. Adding a third status value here would create a second, overlapping
mechanism for the same gradation without giving any consumer a new
decision to make. "Failed" means the outright-exception case only: retries
exhausted, JSON decode failure, or any other exception the parser doesn't
otherwise handle.

`database/models.py::InboundOrder` gains a matching column, migration
`0008_parse_status`:

```python
parse_status = Column(String(20), nullable=False, default="success")
```

Written from `parsed.parse_status` in `_process_inbound`'s Step 2 (order
persist), same place `parse_error`/`parse_confidence` are already written.

### 2. Matching is skipped entirely when `parse_status == "failed"` — not run-and-discard

`api/app.py::_process_inbound` gates Step 3 (Match) and Step 4 (Persist
matches) on `parsed.parse_status`:

```python
if parsed.parse_status == "failed":
    ranked = []
    log.warning("pipeline.parse_failed_skip_matching", order_id=str(order_id))
else:
    signal_client = SignalCacheClient(session)
    vessels = signal_client.get_available_vessels()
    ranked = matching_engine.rank(vessels, parsed)
    for sv in ranked:
        session.add(MatchResult(...))
    session.commit()
```

This is a deliberate **skip**, not "run `rank()` and throw the results
away" — no `SignalCacheClient.get_available_vessels()` call, no
`matching_engine.rank()` call, no `MatchResult` rows written at all for a
failed-parse order. Three reasons, in order of importance:

1. **Correctness.** `matching_engine.rank()` scoring every candidate
   vessel at a flat neutral value against a blank order is not a
   computation with a meaningful result — there is nothing to compute.
   Persisting it as if it were real ranked output is the exact bug this
   ADR exists to fix; not calling `rank()` at all is more honest than
   calling it and hiding the output.
2. **No wasted work.** A failed parse touching the Signal cache and
   running the full scoring loop over every cached vessel for no usable
   outcome is pure waste, however cheap in absolute terms today.
3. **A clean absence-of-`MatchResult`-rows invariant becomes available for
   free** — see Decision 3's discussion of why this still isn't used as
   the sole signal on the API surface, but it is a nice, honest side
   effect: a failed-parse order genuinely has zero match rows, not
   "zero real match rows plus five fabricated ones."

**Step 5 (Push) still runs unconditionally**, including on failure — the
broker's panel should still learn a new message arrived in real time even
if it couldn't be parsed; silently not pushing anything for a failed order
would just trade one invisibility problem for another. The WebSocket
payload built by `_push_to_websockets` gains `"parse_status"` and
`"parse_error"` inside its existing `"parsed"` sub-object, and `"matches"`
is simply `[]` (already true mechanically once Step 3/4 are skipped — no
separate branch needed there).

**Precise definition of "fails outright"** (both existing `except`
branches in `parse_message()`, unchanged by this ADR): a `JSONDecodeError`
parsing the LLM's response, or any other exception — which in practice
today means `tenacity`'s `RetryError` after `_call_llm`'s 3 attempts are
exhausted (auth failures, rate limits, network errors, any Anthropic SDK
exception that doesn't resolve within the retry budget). **Not** included:
a structurally valid, successfully-parsed JSON response, however low every
field's confidence is — that is `parse_status = "success"` by definition,
per Decision 1.

### 3. API surface: explicit `parse_status` field, not inference from absent `MatchResult` rows

Both `GET /orders/latest` (the per-order dict) and `GET /matches/{order_id}`
(`MatchResponse`) gain `parse_status: str` and `parse_error: Optional[str]`
fields, read directly off `InboundOrder.parse_status` /
`InboundOrder.parse_error`.

**Explicit field, not "frontend infers failure from an empty `matches`
list," even though Decision 2 makes that inference technically valid for
newly-ingested orders.** Two reasons this project already has a legitimate
empty-`matches`-but-successfully-parsed case, and conflating them would
recreate a milder version of the same ambiguity this ADR fixes:

- `matching_engine.rank()` already returns `[]` and logs
  `matching_engine.no_vessels` when `SignalCacheClient.get_available_vessels()`
  comes back empty — e.g. the Signal cache hasn't refreshed yet, or (per
  CLAUDE.md's documented 2026-07-30 bug) a refresh cycle failed and
  correctly declined to touch the cache. A successfully-parsed order with
  zero candidate vessels available is a real, meaningfully different state
  from a failed parse, and the frontend needs to say something different
  in each case ("no vessels currently available in the cache" vs. "this
  message could not be parsed").
- Relying on row-absence also silently breaks the moment anyone
  reconsiders Decision 2 later (e.g. persisting a diagnostic placeholder
  row) — an explicit field is not coupled to that implementation detail.

`parse_error` is included alongside `parse_status` (not status alone)
following this codebase's own established pairing convention
(`OutboundMessage.send_status`/`error_message`,
`SignalCacheRefresh.success`/`error_message`) — the frontend doesn't have
to show it, but the backend shouldn't make the frontend re-fetch or guess
at the underlying reason if it wants to.

### 4. Frontend: a distinct failed-parse state, not a degraded version of the match view

**`middleware_frontend/src/components/OrderCard.jsx`** (list view) checks
`order.parse_status === "failed"` before falling into its existing
`has_low_confidence_fields`-amber-dot / `cargo_type || "Unknown cargo"`
path. On failure: a red (not amber) dot using the existing `--red`
token already defined for low score chips, and the title text becomes
`"Parsing failed — needs review"` instead of `"Unknown cargo"` (which today
reads as "the broker successfully parsed this and there's just no cargo
type," an accidentally misleading message for this exact case). No score
chip renders — already true mechanically (`top_match` is `null`), no
change needed there, but the absence now correctly means "nothing to show"
rather than "coincidentally no vessels beat the cutoff."

**`middleware_frontend/src/components/MatchPanel.jsx`** branches before
rendering `ParsedOrderSummary` + the vessel list: when
`order.parse_status === "failed"`, render a new
**`FailedParseNotice.jsx`** component instead of both. `ParsedOrderSummary`
is not reused/degraded for this case — its "confidence pill" showing "0%
confidence" would still read as a computed judgment, exactly the same
failure mode this ADR is fixing one level up.

`FailedParseNotice.jsx` (new file, same visual language as the existing
`--red` failure treatment `SentItem.jsx` already uses for failed sends):

- A clear red banner: `"Unable to parse this message"` with a broker-facing
  explanation, not the raw exception text (`order.parse_error`'s raw
  `RetryError[...]` repr is developer-facing) — surfaced instead the same
  way `SentItem.jsx` already surfaces `OutboundMessage.error_message`: as
  hover/title text on a small details affordance, not inline body copy.
- The raw message body (`order.raw_body`), always shown — not behind the
  existing `showRaw` toggle used for successfully-parsed orders — since
  for a failed parse the raw body **is** the only information the broker
  has; making them click to reveal it would be actively unhelpful here.
- No vessel list, no "Top N vessels" header, no score chips — replaced by
  a short explanatory line: `"Vessel matching was not run because this
  message could not be parsed. Review the message below."`
- The existing Reply button/`ComposeReply.jsx` flow is still available
  and unchanged — it only ever depended on `order.raw_body` and thread
  metadata, never on parsed fields, so a broker can still manually reply
  to a failed-parse order exactly as before.

### 5. Backward compatibility: migration includes both schema change and a data cleanup, not just forward-looking behavior

Verified directly against this environment's own dev DB (not assumed):
**5 existing `InboundOrder` rows already have `parse_confidence == 0` /
`parse_error` populated, and 25 `MatchResult` rows already exist against
them** — exactly the fabricated-50%-match rows this ADR exists to prevent,
already sitting in the local database from the current broken-API-key
state. This is not purely forward-looking; `coder`'s migration
(`0008_parse_status`) does three things in one pass:

1. `ALTER TABLE inbound_orders ADD COLUMN parse_status VARCHAR(20) NOT
   NULL DEFAULT 'success'` (schema).
2. `UPDATE inbound_orders SET parse_status = 'failed' WHERE parse_error IS
   NOT NULL` (backfill — reclassifies every existing row using the signal
   that was already reliable per this ADR's Context section, so no
   existing row is left mis-tagged `'success'` by the column's own
   default).
3. `DELETE FROM match_results WHERE order_id IN (SELECT id FROM
   inbound_orders WHERE parse_status = 'failed')` (cleanup — removes
   exactly the 25 fabricated rows this ADR's investigation found, and any
   equivalent rows in any other environment this migration runs against).

Step 3 is a genuine, irreversible data deletion, not a reversible schema
change — flagged explicitly rather than folded in silently. It's the
correct default here (these rows are, by this ADR's own finding,
meaningless — keeping them "just in case" preserves fabricated data, not
useful history) but `coder` should confirm against whichever environment
this runs in that nothing downstream (e.g. an analytics query, though per
ADR 0006 nothing external consumes `match_results` today) depends on their
continued presence before applying it outside local dev.

## Options considered

### Signal design: infer from `parse_error`, or add an explicit `parse_status` field

| Option | Description | Outcome |
|---|---|---|
| **A. Infer failure from `parse_error IS NOT NULL`, no new column** | Every consumer (API handlers, WebSocket payload builder, any future consumer) independently checks `order.parse_error is not None`. | Rejected. Works today, but pushes the same inference logic into multiple call sites and conflates "the stable contract" with "an exception repr string," against this codebase's own established pattern of pairing an explicit status field with a detail field. |
| **B. Explicit `parse_status` enum with three values (`success`/`partial`/`failed`)** | Matches the task's own suggested shape literally. | Rejected. No code path in this pipeline produces a state distinct from "success" and "failed" today — a weak-confidence-but-structured parse is already handled by the existing per-field `has_low_confidence_fields` mechanism. A third value would be dead/unreachable or would require inventing a new threshold-based rule with no current motivating case, which is speculative design this ADR doesn't have grounds for. |
| **C. Explicit `parse_status` with two values, derived once in the parser (chosen)** | `ParsedOrder.parse_status` set once, in `parse_message()`, at the same place `error`/`parse_confidence` are already set; persisted and exposed as-is everywhere downstream. | **Accepted.** One source of truth, computed exactly where the outcome is actually known, matches this codebase's `send_status`/`success` field-pairing convention, and doesn't invent a state the pipeline can't produce. |

### Whether to still run matching against a failed parse

| Option | Description | Outcome |
|---|---|---|
| **A. Run matching as today, flag the result as unreliable in the UI only** | Keep `_process_inbound` unchanged; add a frontend-only warning banner on top of the existing 50%-everywhere match list. | Rejected. Still computes and persists genuinely meaningless `MatchResult` rows (wasted Signal-cache read + scoring pass), and still requires the frontend to correctly interpret a warning banner *and* five identical-looking score chips without contradicting each other — strictly worse than not producing the misleading data in the first place. |
| **B. Skip matching and persistence entirely when parse fails outright (chosen)** | This ADR, Decision 2. | **Accepted.** No wasted computation, no fabricated rows to ever mis-render, and the empty-`matches` state becomes a true fact about the order rather than something the frontend has to explain away. |

## Reasoning

- **The core problem is a missing distinction, not a scoring-formula bug.**
  `matching/engine.py`'s neutral-0.5 default is correct and intentional
  behavior for a *successfully parsed* order with individually missing
  fields (CLAUDE.md documents this explicitly as "avoids unfairly
  penalising vessels/orders with gaps") — this ADR does not touch that
  logic. The bug is entirely upstream: nothing before the scoring stage
  ever asked "did we actually have real data to score, at all," so the
  same neutral-default machinery designed for *partial* gaps got applied
  to a *total* absence of data and produced output indistinguishable from
  a real borderline case.
- **Reusing `parse_error`'s existing reliability, rather than inventing a
  new detection mechanism, keeps this fix small and traceable.** The
  investigation confirmed the underlying signal (`parse_error IS NOT
  NULL`) was already 100% correlated with the failure case in this
  environment's actual data before any code changed — the fix is making
  that signal a first-class, stably-named contract field, not discovering
  a new one.
- **Skipping matching outright (Decision 2) rather than computing-and-
  hiding is the more conservative choice under this project's own
  demonstrated pattern.** CLAUDE.md's Signal-cache-refresh fix (2026-07-30,
  same day) made exactly this call already: on partial/total failure,
  *skip* the destructive step (`_prune_stale()`) rather than run it and
  hope downstream code hides the bad result. This ADR applies the same
  discipline one layer up the pipeline.
- **Not collapsing "low confidence" and "failed" into the same UI
  treatment matters as much as separating "failed" from "borderline
  match."** A broker who sees every failed-parse order rendered
  identically to a low-but-real-confidence order has learned nothing new;
  the whole point is three visibly distinct states in the list view
  (confident match / flagged-low-confidence match / failed, needs manual
  review), not two.

## Consequences

### To build (handoff to Agent 3 — Coder)

- `middleware/parser/llm_parser.py`: add `parse_status: str = "success"`
  to `ParsedOrder`; set to `"failed"` in both existing `except` blocks in
  `parse_message()`.
- `middleware/database/models.py`: add `InboundOrder.parse_status =
  Column(String(20), nullable=False, default="success")`.
- `middleware/database/migrations/versions/0008_parse_status.py`: add
  column (schema), backfill existing rows from `parse_error IS NOT NULL`
  (data), delete existing `MatchResult` rows for now-`"failed"` orders
  (data cleanup) — all three steps, per Decision 5.
- `middleware/api/app.py::_process_inbound`: gate Step 3/4 on
  `parsed.parse_status == "failed"` per Decision 2; write
  `parse_status=parsed.parse_status` in the Step 2 `InboundOrder(...)`
  constructor call.
- `middleware/api/app.py::_push_to_websockets`: add `"parse_status"` and
  `"parse_error"` to the `"parsed"` sub-object of the `NEW_MATCHES`
  payload.
- `middleware/api/app.py`: add `parse_status: str` and `parse_error:
  Optional[str]` to `MatchResponse`; populate both in `get_matches()`; add
  the same two keys to the per-order dict in `get_latest_orders()`
  (`/orders/latest`).
- `middleware/database/seed.py`: its `InboundOrder(...)` construction
  (around line 453) bypasses `parse_message()` entirely and must set
  `parse_status="success"` explicitly for its seeded rows (all currently
  hardcode high `parse_confidence` values, 0.87–0.96, with no failure
  case) — otherwise seed data silently relies on the column default rather
  than being explicit, and seed.py should also gain at least one seeded
  `parse_status="failed"` row so frontend work on this ADR has realistic
  fixture data to develop against without needing a broken API key.
- `middleware_frontend/src/components/OrderCard.jsx`: branch on
  `order.parse_status === "failed"` per Decision 4 (red dot, "Parsing
  failed — needs review" text, in place of the existing amber-dot/"Unknown
  cargo" path for this case specifically).
- `middleware_frontend/src/components/MatchPanel.jsx`: branch on
  `order.parse_status === "failed"` to render the new
  `FailedParseNotice.jsx` instead of `ParsedOrderSummary` + the vessel
  list.
- `middleware_frontend/src/components/FailedParseNotice.jsx` (new file):
  per Decision 4 — red banner, always-visible raw body, no vessel list,
  reuses the existing `ComposeReply.jsx` flow unchanged.

### To retire

Nothing existing is removed. `parse_error`/`parse_confidence` stay exactly
as they are today — `parse_status` is additive, not a replacement for
either.

### Unchanged (explicit scope boundary)

- `matching/engine.py`'s neutral-0.5 fallback logic — correct and
  unmodified; this ADR fixes when it runs, not what it computes.
- `mail/smtp_client.py`, `ComposeReply.jsx`, the reply-send path in
  general — a failed-parse order remains fully repliable; nothing about
  send/threading depends on parse success.
- `GET /status`, `SignalCacheRefresh`, the Signal-cache-refresh fix
  documented in CLAUDE.md — a separate, already-resolved failure mode;
  this ADR's `parse_status` is specific to the LLM parsing stage, not
  Signal Ocean cache health.
- `database/cli.py` — no new CLI command needed; migration `0008` runs
  through the existing `python -m database.cli migrate` path like 0001–0007.

## Open questions (not decided here)

1. **Should a failed-parse order ever be retried automatically** (e.g. a
   scheduler job that re-runs `parse_message()` against
   `InboundOrder.raw_body` for `parse_status = "failed"` rows once
   `ANTHROPIC_API_KEY` or a transient outage is fixed), rather than
   requiring the broker to notice and handle it manually? Not addressed
   here — this ADR only makes the failure state visible and stops it from
   producing fabricated matches; an automatic-retry mechanism is a
   separate design question with its own tradeoffs (duplicate-order risk
   if retried against the same `source_message_id`, staleness of a
   re-parse run hours after the original message arrived). Route to
   `architect` if this becomes a real ask.
2. **Whether a third `parse_status` value will eventually be needed** —
   e.g. distinguishing "the Anthropic API itself is down/misconfigured"
   (an infrastructure problem, potentially affecting every inbound message
   until fixed) from "this one specific message was malformed enough that
   even a healthy parser couldn't extract it" (a per-message problem). Both
   collapse to `"failed"` today because `parse_message()` doesn't
   distinguish them internally either. Not invented here per this ADR's
   own reasoning in Decision 1 (no motivating code path yet) — but
   flagged, since CLAUDE.md's own Open Questions table shows exactly this
   kind of "the API key might be invalid vs. genuinely down vs. a bad
   single message" ambiguity is already live in this environment, and a
   future briefing/debug pass finding many `"failed"` rows in a row (echoing
   the Signal-cache-refresh incident's "7 consecutive cycles, all green"
   pattern) might want to distinguish "every message is failing" from "one
   odd message failed."
3. **Whether `OrderList.jsx`'s folder/tab or sort logic should surface
   failed-parse orders differently** (e.g. a dedicated filter, or sorting
   them to the top as needing attention) beyond the per-row visual
   treatment in `OrderCard.jsx` decided here. Not addressed — this ADR
   scopes the minimum fix (make the state visible and non-misleading
   wherever an order already renders), not a new triage workflow.

## References

- `.claude/decisions/0006-analytics-data-export-scope.md` — precedent for
  this ADR's format and for treating a data-cleanup/backfill migration step
  as a first-class decision, not an afterthought.
- CLAUDE.md, "Signal Ocean integration" section, 2026-07-30 entry — the
  same-day precedent this ADR's Decision 2 reasoning explicitly follows
  (skip the destructive/misleading step on partial or total failure rather
  than completing it and hiding the result).
- `middleware/parser/llm_parser.py`, `middleware/api/app.py::_process_inbound`,
  `middleware/matching/engine.py`, `middleware/database/models.py` — read
  in full for this ADR; current behavior verified directly, including
  against this environment's actual dev database contents (5 zero-confidence
  orders, 25 associated fabricated `MatchResult` rows, confirmed by direct
  query, not assumed).
- `middleware_frontend/src/components/OrderCard.jsx`,
  `MatchPanel.jsx`, `ParsedOrderSummary.jsx`, `SentItem.jsx` — current
  structure and the existing failed/error visual-treatment precedent
  (`SentItem.jsx`'s red "Failed" + hover `error_message`) this ADR's
  Decision 4 follows.
