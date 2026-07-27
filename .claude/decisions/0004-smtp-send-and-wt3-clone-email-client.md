# ADR 0004: SMTP Reply Sending and Full WT3 Clone Email Client Scope

**Status:** Accepted
**Date:** 2026-07-27
**Deciders:** Architect agent (design), per user direction to scope the
outbound-send capability and the full "WT3 Clone Email Client" build
(PRD §11.2) ahead of implementation

## Context

A ground-truth investigation (not itself an ADR) established the current
state precisely, and it is narrower than CLAUDE.md's "companion panel"
description might suggest to a casual reader:

- `middleware_frontend/` has **zero email-client capability** beyond the
  existing companion panel — no threading, no reply/compose, no
  inbox/folder/read-state management anywhere in frontend or backend.
- The IMAP layer is **read-only end to end, with no SMTP/send path at
  all**. Verified directly:
  - `middleware/mcp/imap_client.py:24` — `conn.select("INBOX",
    readonly=True)` — and the module docstring (`imap_client.py:9-10`)
    states this in maritime-middleware's own words: *"Read-only — this
    only ever reads the inbox, per PRD non-goal 'will NOT send emails'."*
    This is a deliberate boundary, not an oversight.
  - `middleware/mcp/imap_client.py:52-63` (`_parse_message`) captures only
    `id` (IMAP UID), `sender` (`From`), `subject`, `received_at` (`Date`),
    and `raw_body`. It does **not** capture `Message-ID`, `In-Reply-To`,
    or `References` — the RFC 2822 headers needed for real threading.
    `middleware/mcp/mock_inbox.py`'s `MOCK_EMAILS` fixtures likewise carry
    no such headers. Threading has to be built from scratch on the capture
    side, not just the UI side.
  - `middleware/api/app.py` (`ingest`, `get_matches`, `get_latest_orders`,
    `get_status`, `websocket_endpoint`) exposes no send endpoint of any
    kind.
  - `middleware_frontend/src/components/` has seven components
    (`StatusBar`, `OrderList`, `OrderCard`, `MatchPanel`,
    `ParsedOrderSummary`, `DealCard`, `ScoreBar`) — none is a compose or
    reply UI.
- **A pre-existing, unrelated config bug worth flagging here because this
  ADR's SMTP config is explicitly asked to mirror the IMAP credential
  pattern**: `imap_client.py:22-23` reads `settings.IMAP_HOST`,
  `settings.IMAP_PORT`, `settings.IMAP_USER`, `settings.IMAP_PASS`
  (uppercase attribute access), but `middleware/config.py`'s `Settings`
  class never defines fields with those names — only
  `imap_poll_interval_minutes` / `imap_poll_batch_size` exist in the IMAP
  section (`config.py:34-39`), and `.env.example` likewise never defines
  `IMAP_HOST`/`IMAP_PORT`/`IMAP_USER`/`IMAP_PASS` (only the poll-tuning
  vars, `.env.example:27-30`), even though PRD §12.3 documents those four
  as required env vars. Since pydantic `BaseSettings` instances don't
  expose undeclared attributes, `IMAP_MODE=live` almost certainly raises
  `AttributeError` today. This is the same *class* of bug ADR 0002 flagged
  for `mcp/signal_client.py`'s `settings.SIGNAL_OCEAN_API_KEY` — not this
  ADR's problem to fix, and not blocking anything today since `IMAP_MODE`
  defaults to `mock`. It matters here only because "mirror the existing
  IMAP credential pattern" must mean mirroring the *intended* pattern
  (lowercase fields properly declared on `Settings`), not this broken
  instance of it. Flagged to `coder` as a low-cost fix-while-you're-there,
  not a blocker for this ADR.
- **PRD internal contradiction.** §1.4 ("Non-Goals") states: *"The system
  will NOT send emails, confirm fixtures, or communicate with
  counterparties."* §11.2 ("To Build") describes the "WT3 Clone Email
  Client (full)" as including *"reply composition."* These conflict the
  moment reply composition is wired to an actual send path, which is
  exactly what this ADR designs. This ADR resolves the contradiction (see
  Decision #2) — it does not modify the PRD document itself, per the
  constraint that the PRD is not to be edited; the corrected non-goal
  language lives here and in the future CLAUDE.md update that follows
  implementation.
- **Relationship to CLAUDE.md.** CLAUDE.md's "Plan vs. reality" table
  currently reads: `UI | Full WT3 UI clone | Companion Electron panel
  beside WT3 | Unchanged — companion panel, not a clone.` That row
  accurately describes *today's built state* — nothing described in this
  ADR has been implemented yet, so the row remains true as written. This
  ADR is not a correction of that row; it is a target-architecture design
  for a not-yet-started future build, produced ahead of implementation in
  the same sequence already established by ADR 0002 (wiring design written
  and accepted before the poller existed) and ADR 0003 (bug-fix design
  written and accepted before the fix landed). Per that established
  pattern, **CLAUDE.md is not updated by this ADR** — it gets updated once
  (if) `coder` actually builds some or all of this scope.
- **Why not GUI automation of Telix WT3 itself.** ADR 0001 already
  rejected the AutoHotkey clipboard-watcher class of solution (client-side
  trigger automation against WT3's UI) as a *transitional, not long-term*
  mechanism, precisely because it inherits WT3's own reliability and
  timing fragility as a dependency. No API exists for driving WT3
  programmatically; the only two ways to make WT3 itself send a reply are
  (a) scripted UI automation (keystrokes/clipboard, i.e. the AHK
  approach's send-side twin) or (b) not touching WT3 at all and using the
  mailbox directly. Given (a) was already rejected on the read side for
  exactly this reliability reason, it would be inconsistent to accept it
  on the write side. This ADR chooses (b).

## Decision

### 1. Outbound replies are sent via SMTP, using the broker's real mailbox credentials

The clone email client sends outbound replies via SMTP, symmetric with the
existing IMAP-based read approach — **not** GUI automation of Telix WT3.
SMTP send uses the same mailbox account (and, in practice, the same
username/password) the IMAP layer already reads from — the broker's real
inbox. A sent reply is therefore indistinguishable, to the counterparty,
from one sent through Telix WT3 itself: same `From` address, same outbound
mail server, same RFC 2822 shape. This is a new, additive capability — it
does not change anything about how the IMAP side reads (still read-only,
still `readonly=True`).

### 2. Corrected non-goal (resolves the PRD §1.4 vs §11.2 contradiction)

The corrected non-goal, to be reflected wherever this scope is documented
going forward (this ADR now; CLAUDE.md and, if ever revised, the PRD,
later):

> The system does not send emails autonomously or without explicit broker
> action. Every send is a broker-initiated, broker-reviewed action through
> the clone's compose UI, identical in effect to the broker typing and
> hitting send in Telix WT3 today. The system still never fixtures,
> negotiates, or acts as a counterparty on its own initiative.

Only the "will not send emails" clause of PRD §1.4 is corrected, to "will
not send emails **without the broker explicitly composing and sending
them**." The rest of §1.4 (no fixture confirmation, no autonomous
negotiation, no counterparty communication *initiated by the system*)
stands unchanged and is in fact reinforced by requiring every send to be a
manual, reviewed broker action — there is no automated or agentic send
path anywhere in this design.

### 3. Email threading — RFC 2822 headers, not subject heuristics

Capture and use `Message-ID`, `In-Reply-To`, and `References` headers.
Reject subject-line heuristic threading and reject shipping-with-no-
threading. See Options/Reasoning for the tradeoff analysis. Concretely:

- Extend `_parse_message` in `imap_client.py` (and the `MOCK_EMAILS`
  fixtures in `mock_inbox.py`) to also return `message_id`,
  `in_reply_to`, and `references` (raw header strings; `references` may
  contain a space-separated list per RFC 2822 §3.6.4).
- Add `message_id`, `in_reply_to`, `references`, and `thread_id` columns
  to `inbound_orders` (see Consequences for the migration). `thread_id`
  is computed at ingest time: if `in_reply_to` (or the last id in
  `references`) matches an existing row's `message_id`, reuse that row's
  `thread_id`; otherwise `thread_id := message_id` (this message starts a
  new thread). This trades a small amount of ingest-time lookup for O(1)
  thread-grouping reads later (`GET /orders/latest` and a new
  `GET /threads/{thread_id}`), rather than recomputing the reference chain
  on every read.
- Outbound sends (Decision #1) must set `In-Reply-To` and `References`
  correctly when replying, both so counterparty mail clients thread
  correctly and so our own sent replies participate in `thread_id`
  computation when the counterparty's reply-to-our-reply arrives back.

### 4. Reply composition UI

New `middleware_frontend/src/components/ComposeReply.jsx`. Rendered
inside `MatchPanel.jsx` (which already owns the right-pane layout hosting
`ParsedOrderSummary` + the `DealCard` list), triggered by a "Reply"
affordance added to `ParsedOrderSummary.jsx`'s header row. Fields:

- **To**: prefilled from `order.sender`, editable.
- **Subject**: prefilled `Re: {order.subject}` with de-duplication of
  repeated `Re:` prefixes.
- **Quoted original**: attribution line (`On {received_at}, {sender}
  wrote:`) followed by the original `raw_body`, quoted — reusing the same
  raw-body data `ParsedOrderSummary.jsx` already renders in its
  expandable raw-body toggle (`ParsedOrderSummary.jsx:168-177`). Note for
  whoever implements this: that expandable view currently depends on
  `order.raw_body` being present on the object the frontend already has;
  `MatchResponse` (`middleware/api/app.py:82-108`) does **not** currently
  include `raw_body` (a gap already flagged elsewhere against FR-24) — the
  compose view's quoting feature will hit the same gap and needs that
  field added to `MatchResponse` as part of whichever work item fixes it.
  Not re-designing that fix here; noting the dependency.
- **Body**: plain textarea for the broker's reply text.
- **Send**: calls the new `POST /internal/send` (Decision #5). Disabled
  while in flight; on failure shows an inline, persistent error state (not
  a toast that disappears) — see Decision #5 on why this must not follow
  ADR 0003's fire-and-forget pattern.

`App.jsx` gains compose-open/closed state alongside its existing
selected-order state, per the existing convention that `App.jsx` owns
cross-component UI state (PRD §7.3, `src/App.jsx` description: "Root.
WebSocket connection, selected order state, layout orchestration").
`src/lib/api.js` gains a `sendReply(payload)` function, symmetric to the
existing `getLatestOrders` / `getMatches` / `getStatus` /
`subscribeToMatches` functions, and is (per the existing convention
stated in CLAUDE.md) the only place in the frontend that talks to the
backend.

### 5. SMTP sending mechanism

- **Library**: stdlib `smtplib` + `email.message.EmailMessage`. No new
  third-party SMTP wrapper dependency. This mirrors the existing design
  instinct in `imap_client.py`, which uses stdlib `imaplib` rather than an
  external IMAP wrapper — same reasoning applies symmetrically to the send
  side, and it keeps `requirements.txt` (PRD §6.2) from growing for a
  well-solved stdlib problem.
- **Credentials**: new `.env` vars mirroring the *intended* IMAP pattern
  (see Context's flag on the existing IMAP config bug) —
  `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_USE_TLS`.
  Kept as separate vars from `IMAP_USER`/`IMAP_PASS` (even though in
  practice they will usually hold the same broker credentials) rather than
  silently reusing the IMAP settings object, because the IMAP host and
  SMTP host are frequently different hostnames on the same mail provider
  (e.g. `imap.brokerfirm.com` vs `smtp.brokerfirm.com`), and because
  collapsing them would mean a future IMAP credential rotation silently
  and invisibly changes SMTP behavior too. These must be declared as
  proper lowercase fields on `config.py`'s `Settings` class (`smtp_host:
  str`, `smtp_port: int = 587`, `smtp_user: str`, `smtp_pass: str`,
  `smtp_use_tls: bool = True`) — not the broken uppercase
  attribute-that-doesn't-exist pattern currently seen in
  `imap_client.py`.
- **Endpoint**: `POST /internal/send`, following the existing
  `/internal/*` naming convention for backend-internal action endpoints
  (`/internal/ingest` is the only current member of that namespace).
  Request body: `sender`/`to`/`cc?`/`subject`/`body`, plus optional
  `in_reply_to_order_id` (FK context for the UI) and the RFC headers
  needed for threading (Decision #3). **Unlike `/internal/ingest`, this
  endpoint is synchronous — no `BackgroundTasks`, no `202 Accepted`.** It
  returns `200` with `{status: "sent", message_id: ...}` on success, or a
  clear `4xx`/`5xx` with an error detail on failure. This is a deliberate
  and important divergence from both the ingest endpoint's fire-and-forget
  `202` pattern and ADR 0003's fire-and-forget WebSocket broadcast
  pattern: ingestion and broadcast are unattended background processes
  where the broker isn't watching and waiting, so "log and move on" is
  correct; sending a reply is a broker-initiated action where the broker
  is watching a Send button and needs to know, definitively and
  immediately, whether the counterparty will actually receive that email.
  A silently-swallowed SMTP failure here would be a materially worse
  outcome than a silently-swallowed WebSocket push, because the broker
  would believe a reply went out when it didn't.
- **Error handling**: `smtplib` exceptions (auth failure, connection
  refused, recipient refused, etc.) are caught in the endpoint handler
  and mapped to a response the compose UI can render as a blocking error
  state — the broker must be told the send failed and be able to retry,
  not left assuming success.
- **Security / NFR exception**: NFR-07 ("all traffic MUST remain on
  localhost") and NFR-09 ("email content MUST NOT leave the local machine
  except via the Anthropic API for parsing") describe the *inbound,
  read/parse* path correctly today. SMTP send is an explicit, necessary
  exception to NFR-09's letter (not its spirit): sending mail inherently
  requires the reply content to leave the local machine and reach the
  broker's SMTP server — there is no way to send an email without this.
  NFR-07 is unaffected (no *inbound* port is opened; the backend still
  only accepts connections on localhost — it makes an *outbound*
  connection to the SMTP server, exactly as it already makes outbound
  connections to `api.anthropic.com`, `apis.signalocean.com`, and the
  IMAP server today). NFR-08 (API keys / secrets in `.env`, never
  hardcoded) applies to `SMTP_USER`/`SMTP_PASS` exactly as it already
  applies to `IMAP_USER`/`IMAP_PASS`. This ADR records the NFR-09
  exception explicitly so it reads as a deliberate, scoped decision rather
  than a silent violation discovered later.

### 6. Inbox / folder management

Minimum viable folder concept: **Inbox** (unchanged — the existing
`inbound_orders` table and IMAP read path) and **Sent** (new,
`outbound_messages` table, populated only by the backend's own successful
`/internal/send` calls). No general-purpose folder abstraction, no sync
against arbitrary real IMAP folders beyond `INBOX`. Two separate tables
rather than one unified `messages` table with a `direction`/`folder`
column, because inbound messages flow through the parse → match → persist
pipeline and carry columns (parsed fields, confidence scores, match
results) that outbound messages structurally don't have and shouldn't be
forced into; a shared table would mean every inbound-shaped column is
nullable-and-unused on every Sent row, for no benefit at this scope.
Whether the real IMAP `Sent` folder on the broker's mailbox also gets an
`APPEND` of what we sent (so WT3 itself shows the reply in its own Sent
view, matching what typing-and-sending directly in WT3 would produce) is
an **open question**, not decided here — see Open Questions. Doing so
would require a second, explicitly write-capable IMAP connection distinct
from the existing always-`readonly=True` one, which is a bigger design
surface than this ADR's headline scope.

### 7. Read/unread state — local Postgres only, not synced to real IMAP `\Seen`

Add `is_read: Boolean = False` to `inbound_orders`, mutated only by the
clone's own UI action (opening an order marks it read locally). **Do not**
sync to the mailbox's real `\Seen` flag. See Reasoning for why; the
tradeoff (the clone and WT3 will show independent, potentially
inconsistent read/unread state for the same message) is accepted
explicitly, not silently ignored.

## Options considered

### Outbound send mechanism

| Option | Description | Outcome |
|---|---|---|
| **A. GUI automation of Telix WT3** | Script WT3's own UI (keystrokes/clipboard) to compose and send, the send-side mirror of the already-rejected AHK read-side trigger. | Rejected — inherits exactly the reliability/timing fragility ADR 0001 already rejected for reading; no API exists for WT3 to do this any other way; would reintroduce a client-side automation dependency this project has already moved away from once. |
| **B. SMTP with the broker's real mailbox credentials (chosen)** | Direct `smtplib` send from the backend, using the same account the IMAP layer reads from. | **Accepted.** Symmetric with the existing read design, no new external dependency, output is indistinguishable from a WT3-sent email to the counterparty. |
| **C. Third-party transactional email API/relay** (e.g. SendGrid, SES) | Send through a relay service instead of the broker's own mailbox. | Rejected — the reply would come from a different sending infrastructure than the broker's real mailbox (SPF/DKIM alignment, `From` reputation), breaking the "indistinguishable from WT3" requirement, and introduces a new vendor/API-key dependency (violating the "one mailbox, one set of credentials" simplicity the IMAP side already established). |
| **D. No send capability; stay read-only forever** | Keep the panel as a pure companion/matching tool indefinitely. | Rejected as the target — this is what's built *today* and remains valid as an intermediate state, but the user has explicitly asked to scope the full clone per PRD §11.2, which requires a send path to exist. |

### Threading

| Option | Description | Outcome |
|---|---|---|
| **A. RFC 2822 `Message-ID`/`In-Reply-To`/`References` (chosen)** | Capture headers on ingest, set them correctly on send, group by computed `thread_id`. | **Accepted.** Standard, robust to subject edits, correctly handles replies-to-replies; the marginal build cost is small because outbound send already needs to set these headers correctly to be a well-behaved SMTP citizen, so the inbound capture/storage side is the only genuinely new piece. |
| **B. Subject-line heuristic** | Group by normalized subject (strip `Re:`/`Fwd:` prefixes, fuzzy-match). | Rejected as primary mechanism — fragile against subject edits and, per the maritime shorthand seen in `mock_inbox.py`'s own fixtures (e.g. generic subjects like `ENQ`), prone to false-merging unrelated cargo enquiries that happen to share a route or a terse subject. Could still be a fallback for messages missing headers entirely, but not the design of record. |
| **C. No threading at MVP, flat list with a future-work note** | Ship compose/send without any conversation grouping. | Rejected as the target design — the user explicitly asked for threading as one of the five scoped capability areas; deferring it entirely doesn't answer that. Remains a legitimate incremental-delivery *sequencing* choice for `coder` (ship send before thread grouping), just not the end-state design. |

### Read/unread sync

| Option | Description | Outcome |
|---|---|---|
| **A. Sync to real IMAP `\Seen` flags** | Backend marks messages `\Seen` on the actual mailbox when the broker opens them in the clone. | Rejected for now — requires a second, explicitly write-capable IMAP connection alongside the existing, deliberately `readonly=True` one (`imap_client.py:24`), which is a real boundary this codebase chose on purpose (its own docstring cites the PRD non-goal). Also introduces a race: WT3 and the clone would be two independent clients mutating the same mailbox's read state, which could surprise the broker (a message the broker hasn't looked at in WT3 shows as read there because it was opened in the clone, or vice versa). |
| **B. Local Postgres-only `is_read` (chosen)** | New column on `inbound_orders`, mutated only by clone UI actions, never touches the mailbox. | **Accepted.** Keeps the IMAP boundary exactly as read-only as it is today, trivial to build, no new mailbox-mutation failure mode. Tradeoff (divergent read-state between WT3 and the clone) is real and is called out explicitly rather than hidden — see Open Questions for the upgrade path if this proves confusing in practice. |

## Reasoning

- **The send decision is the load-bearing one; everything else in this
  ADR follows from what it implies.** Once "send via the broker's own
  SMTP credentials" is decided, the RFC threading headers are needed
  *anyway* (a reply that doesn't set `In-Reply-To`/`References` correctly
  is a worse citizen in the counterparty's mail client), which is most of
  the argument for choosing RFC threading over a heuristic on the read
  side too — the send-side cost is fixed regardless, so the read-side
  cost of matching it is the only genuinely incremental cost, and it's
  small.
- **Symmetry with the existing IMAP design is a feature, not just an
  aesthetic preference.** `imap_client.py` already establishes the pattern
  of "stdlib protocol library, credentials from `.env`, isolated module
  swappable between mock and live via a mode flag." Following that same
  shape for SMTP (`smtplib`, `SMTP_*` env vars, presumably a
  `smtp_client.py` alongside `imap_client.py`) means whoever built and
  understands the IMAP side can read the SMTP side with zero new mental
  model, and it keeps `middleware/mcp/` and any new `middleware/mail/`
  (or wherever `coder` places it) internally consistent.
- **Synchronous send with a definite result is the correct shape for a
  user-initiated action, and ADR 0003 already established the vocabulary
  for why background/fire-and-forget is the *wrong* shape for this case.**
  ADR 0003's entire fix was built around the principle that a missed
  WebSocket push must never crash ingestion, precisely *because*
  ingestion is unattended and the push is a nice-to-have live-update on
  top of data that's already durably persisted. Sending an email has the
  opposite risk profile: there is no durable side effect if the send
  fails (nothing was persisted representing "sent" until we know it
  actually sent), and the broker is actively watching for the outcome.
  Treating send like ingest (`202` + background task) would silently
  strand the broker believing an email went out.
- **The read-only IMAP boundary is worth preserving rather than casually
  extending.** `imap_client.py`'s `readonly=True` and its docstring
  reference to the PRD non-goal show this was a deliberate design choice,
  not an oversight. Local-only read/unread state achieves the "read/unread
  in the clone" UX requirement without touching that boundary at all,
  whereas syncing `\Seen` would require deciding how a second write path
  (distinct from the also-new SMTP send path) interacts with WT3's own
  IMAP session — a materially bigger design question than "does clicking
  an order in the clone gray it out."
- **Two tables (Inbox/Sent) over one unified `messages` table.** The
  existing `inbound_orders` schema (PRD §8.1) is shaped entirely around
  the parse → match pipeline (cargo fields, confidence scores, port
  normalization). Outbound messages have none of that. Forcing both into
  one polymorphic table would mean either a wide table of
  mostly-null-for-one-direction columns, or a `direction` discriminator
  with conditional logic scattered through every query — more complexity
  than the two-table split for no query-pattern that actually needs a
  unified view at this scope (Inbox and Sent are always browsed
  separately in every mail client, including WT3).

## Consequences

### To build (handoff to Agent 3 — Coder; this ADR is design-only)

**Threading (Decision #3):**
1. `middleware/mcp/imap_client.py` — extend `_parse_message` to also
   extract `msg.get("Message-ID")`, `msg.get("In-Reply-To")`,
   `msg.get("References")` and include them in the returned dict.
2. `middleware/mcp/mock_inbox.py` — add representative `message_id` /
   `in_reply_to` / `references` values to `MOCK_EMAILS`, including at
   least one fixture that is a reply to another fixture, so thread
   grouping has something real to test against.
3. `middleware/database/models.py` — add `message_id` (String, indexed),
   `in_reply_to` (String, nullable), `references` (Text, nullable),
   `thread_id` (String, indexed) to `InboundOrder`. New Alembic migration
   (next sequential version after `0002_source_message_id`).
4. `middleware/api/app.py` — `IngestRequest` and `_process_inbound` gain
   the same optional fields; `_process_inbound` computes `thread_id` by
   looking up `in_reply_to`/`references` against existing `message_id`
   values before persisting.
5. New `GET /threads/{thread_id}` endpoint, or a `thread_id` field added
   to the existing `/orders/latest` response — `coder`'s call which is
   more useful once `OrderList.jsx` is being extended to show threads.

**Reply composition (Decision #4):**
6. New `middleware_frontend/src/components/ComposeReply.jsx`.
7. `middleware_frontend/src/components/ParsedOrderSummary.jsx` — add a
   "Reply" affordance to the header row that raises compose state up to
   `App.jsx` (or `MatchPanel.jsx`, whichever already owns the relevant
   state — `coder`'s call).
8. `middleware/api/app.py` — add `raw_body` to `MatchResponse` (needed for
   quoting; this is the same gap already flagged against FR-24 elsewhere
   — fix it once, here, if not already fixed by then).
9. `middleware_frontend/src/lib/api.js` — add `sendReply(payload)`.

**SMTP send (Decision #5):**
10. New `middleware/mail/smtp_client.py` (or wherever `coder` places
    it alongside `imap_client.py`/`mock_inbox.py`'s existing layout) —
    `smtplib` + `EmailMessage`, mock/live mode symmetric with
    `IMAP_MODE`/`SIGNAL_MODE` (e.g. `SMTP_MODE=mock` logs/no-ops instead
    of connecting, for local dev without real credentials).
11. `middleware/config.py` — add `smtp_host: str`, `smtp_port: int =
    587`, `smtp_user: str`, `smtp_pass: str`, `smtp_use_tls: bool = True`
    as properly declared `Settings` fields (not the broken uppercase
    pattern flagged in Context).
12. `.env.example` — new `SMTP_HOST`/`SMTP_PORT`/`SMTP_USER`/
    `SMTP_PASS`/`SMTP_USE_TLS` section, mirroring the IMAP section's
    layout and comments.
13. `middleware/api/app.py` — new `POST /internal/send`, synchronous,
    `200`/error-detail response shape per Decision #5. New
    `outbound_messages` table + model (Decision #6) written on success.
14. (Optional, low-cost fix-while-here per Context) — fix the pre-existing
    `IMAP_HOST`/`IMAP_PORT`/`IMAP_USER`/`IMAP_PASS` attribute bug in
    `imap_client.py`/`config.py`/`.env.example` while touching this
    section of config for SMTP. Not required by this ADR to be done in the
    same change, but flagged since it's the same file, the same section,
    and the same fix shape.

**Folder management (Decision #6):**
15. `middleware/database/models.py` — new `OutboundMessage` model /
    `outbound_messages` table: `id` (UUID), `in_reply_to_order_id` (FK,
    nullable), `to_addr`, `cc_addr` (nullable), `subject`, `body`,
    `message_id` (our own generated one), `in_reply_to`, `references`,
    `sent_at`, `send_status` (sent/failed), `error_message` (nullable).
    New Alembic migration.
16. New `GET /sent` endpoint, symmetric with `GET /orders/latest`.
17. `middleware_frontend/` — some minimal Inbox/Sent switcher (a new
    `FolderTabs.jsx` or an addition to `StatusBar.jsx`/`OrderList.jsx`) —
    lower priority than compose+send; `coder`/`ceo` can sequence this
    after the send path is proven.

**Read/unread (Decision #7):**
18. `middleware/database/models.py` — `is_read: Boolean = False` on
    `InboundOrder`. Same migration as the threading columns is a
    reasonable batching, `coder`'s call.
19. New `PATCH /orders/{order_id}/read` (or `POST
    /orders/{order_id}/mark-read`) endpoint.
20. `middleware_frontend/src/components/OrderCard.jsx` /
    `OrderList.jsx` — visual read/unread treatment, call the new endpoint
    on order selection.

### Sequencing note (not a phase plan, just an obvious dependency)

SMTP send (items 10-14) must exist before reply composition (items 6-9)
is meaningful — a compose UI with nothing behind its Send button is not
useful to build first. Threading header capture (items 1-5) can proceed
in parallel with SMTP send; the two converge at "outbound sends must set
`In-Reply-To`/`References` correctly," which needs both pieces to exist.
Folder management and read/unread (items 15-20) have no hard dependency on
each other or on threading, and can be built in any order once the
`inbound_orders`/`outbound_messages` schema exists.

### To retire

Nothing. This ADR adds capability; it does not remove or replace any
existing path. The IMAP read path, `readonly=True`, and the existing
ingest/match/persist/push pipeline are all unchanged.

### Unchanged (explicit scope boundary)

- `middleware/mcp/imap_client.py`'s existing read behavior and
  `readonly=True` connection — untouched beyond the additive header
  capture in Decision #3.
- `middleware/parser/llm_parser.py`, `middleware/matching/engine.py`,
  `middleware/signal_client/client.py`, `middleware/scheduler/jobs.py` —
  no changes implied by anything in this ADR.
- CLAUDE.md — not updated by this ADR, per the Context section's
  discussion of ADR 0002/0003 sequencing. Update it once (if) this scope
  is actually built.
- The PRD document itself — not edited. The corrected non-goal language
  lives in this ADR's Decision #2 only.
- The two bugs noted only for reuse-awareness (FR-21's low-confidence-
  fields backend bug at `app.py:383`, and FR-24's missing `raw_body` in
  `MatchResponse`) are not redesigned here. FR-24's `raw_body` gap is
  listed as a concrete build item (#8) only because the compose UI's
  quoting feature directly depends on it; FR-21's bug has no direct
  dependency from anything in this ADR and is left exactly where it was
  already flagged.

## Open questions (not decided here)

1. **Should outbound sends also be `APPEND`ed to the real IMAP `Sent`
   folder** on the broker's actual mailbox, so WT3 itself shows the reply
   in its own Sent view (matching what typing-and-sending directly in WT3
   would produce)? This would require a second, explicitly write-capable
   IMAP connection, a bigger design surface than this ADR's scope. The
   local-only `outbound_messages` table (Decision #6) is sufficient for
   the clone's own Sent view either way; this question is only about
   whether WT3's Sent view also reflects it. Route to `architect` again
   if/when this is prioritized.
2. **Read/unread divergence between WT3 and the clone** (Decision #7,
   Option B accepted): if brokers find having two independent read-states
   for the same mailbox confusing in practice, revisit syncing `\Seen` —
   which then also reopens question #1's write-capable-IMAP-connection
   design, since both would want the same second connection.
3. **Whether `thread_id` should be recomputed/backfilled** if a message
   whose `in_reply_to` was missing/unmatched at ingest time turns out
   later to belong to an existing thread (e.g. out-of-order delivery, or a
   counterparty's mail client omitting headers on the first message in a
   chain but including them later). Not addressed here; likely low
   priority given real mail clients almost universally set these headers
   correctly from the first message.
4. **Attachment handling** for both inbound threading and outbound
   compose is entirely out of scope here — `_extract_plain_text` already
   discards non-text-plain MIME parts on the read side (FR-03), and
   nothing in this ADR proposes changing that. If attachments become a
   requirement, that's a separate ADR.
5. **Multi-broker configuration** (PRD §11.2, listed as its own separate
   low-priority future item, NFR-12) interacts with SMTP credentials the
   same way it would interact with IMAP credentials — each broker inbox
   would need its own `SMTP_*` set, not just its own `IMAP_*` set. Not
   designed here since multi-broker support itself is undesigned; flagged
   so whoever eventually does that ADR knows to extend both credential
   sets together.

## References

- `.claude/decisions/0001-ingestion-imap-mcp.md` — rejected client-side
  WT3 automation (AHK) as a transitional, not long-term, mechanism; this
  ADR applies the same reasoning to reject WT3 automation on the send
  side.
- `.claude/decisions/0002-mcp-ingestion-pipeline-wiring.md` — established
  the ADR-before-CLAUDE.md-update sequencing this ADR follows, and is the
  origin of the "flag a pre-existing config bug without fixing it here"
  pattern applied to the `IMAP_HOST`/`PORT`/`USER`/`PASS` bug in Context.
- `.claude/decisions/0003-websocket-broadcast-thread-safety.md` — origin
  of the fire-and-forget background-task vocabulary this ADR deliberately
  contrasts `/internal/send`'s synchronous design against.
- `docs/maritime_middleware_prd_v1.1.pdf` §1.4 (Non-Goals), §11.2 (To
  Build — WT3 Clone Email Client), §12.3 (IMAP credential env vars) — the
  sections this ADR reconciles or mirrors.
- `middleware/mcp/imap_client.py`, `middleware/mcp/mock_inbox.py`,
  `middleware/api/app.py`, `middleware/config.py`,
  `middleware/.env.example`, `middleware/database/models.py`,
  `middleware_frontend/src/components/ParsedOrderSummary.jsx`,
  `middleware_frontend/electron/preload.js` — current source verified
  directly for this ADR's Context and build-list.
