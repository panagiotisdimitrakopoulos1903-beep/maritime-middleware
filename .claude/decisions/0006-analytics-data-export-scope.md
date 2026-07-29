# ADR 0006: Analytics Data Export — Scope, Anonymisation Design, and Hold-Off Decision

**Status:** Accepted
**Date:** 2026-07-29
**Deciders:** Architect agent (design), scoping PRD §11.2's "Analytics data
export" row ahead of any implementation, per user direction

## Context

PRD §11.2's "To Build" table gives this feature exactly one row, in full:

> **Analytics data export** | Priority: Low | Est. Effort: 2 weeks |
> Aggregated anonymised order flow API. Feeds future commercial data
> product for banks and hedge funds.

That is the entire spec. No endpoints, no schema, no definition of
"anonymised," no named consumer, no access/auth model, no refresh cadence,
no commercial/pricing model. This is a placeholder, not a design brief —
narrower even than ADR 0004's starting point (which at least had a
five-item feature list and an explicit non-goal contradiction to resolve).

**The load-bearing tension this ADR has to resolve.** Two NFRs describe
today's deployment topology:

- NFR-07: "All traffic MUST remain on localhost — no inbound ports exposed
  to the network."
- NFR-09: "Email content MUST NOT leave the local machine except via the
  Anthropic API for parsing."

Both are written for, and today accurately describe, a single-machine,
no-external-exposure tool. "Feeds a future commercial data product for
banks and hedge funds" is definitionally impossible to satisfy without
*some* data leaving this machine and reaching an external party — there is
no version of this feature that doesn't touch NFR-09's letter, the same
way ADR 0004's SMTP send necessarily did. Per that ADR's Decision #5
precedent ("Security / NFR exception"), the right move is to record the
exception explicitly rather than either (a) silently building something
that violates the NFR unremarked, or (b) refusing to reason about it. This
ADR does the reasoning; see Decision #4 for how it resolves, which is
different in kind from ADR 0004's resolution — ADR 0004 was actively
building the SMTP path immediately, so it recorded a live exception. This
ADR defers the actual mechanism (Decision #1), so the exception recorded
here is prospective/conditional, not active — see Decision #4 for exactly
what that means going forward.

**Ground truth on current schema** (`middleware/database/models.py`,
verified directly):

- `InboundOrder` carries parsed cargo fields (`cargo_type`, `quantity_mt`/
  `quantity_min_mt`/`quantity_max_mt`, `load_port`/`load_port_canonical`,
  `discharge_port`/`discharge_port_canonical`, `laycan_start`/`laycan_end`,
  `vessel_size_dwt`, `vessel_type`, `freight_rate`, `charterer`) alongside
  identity/content fields (`sender`, `subject`, `raw_body`, `message_id`/
  `in_reply_to`/`references`/`thread_id`/`source_message_id`) and
  parse-quality metadata (`confidence_scores`, `parse_confidence`,
  `has_low_confidence_fields`, `low_confidence_field_names`, `parse_error`,
  `is_read`).
- `cargo_type` is **free text**, not a constrained enum — confirmed against
  `middleware/matching/engine.py`'s `CARGO_VESSEL_COMPATIBILITY` dict,
  which keys on lowercased free-form strings ("crude oil", "fuel oil",
  "grain", "wheat", "corn", ...). There is no existing small fixed
  category list to reuse as-is.
- `freight_rate` is `Column(String(100))` — **free text, not a validated
  numeric field**, confirmed directly. Maritime freight is quoted in
  incompatible units depending on trade (Worldscale points for tanker
  crude/products, $/mt lumpsum for dry bulk, sometimes flat lumpsum) — this
  column is whatever string the LLM parser extracted, not a normalised
  number with a unit.
- `MatchResult` denormalises a vessel snapshot per match
  (`vessel_id`/IMO, `vessel_name`, `vessel_class`, `dwt`, `open_port`,
  `open_date`) plus the four scoring sub-components.
- `OutboundMessage` (`outbound_messages`) is reply correspondence
  (`from_addr`/`to_addr`/`cc_addr`/`subject`/`body`, `send_status`) —
  confirmed to have no cargo/order-flow fields at all.
- `core/ports.py` already has exactly the geographic taxonomy this feature
  needs: `PORT_TO_AREA` maps ~35 canonical ports to 10 areas (ARA,
  Continent, Baltic, Mediterranean, Black Sea, Middle East Gulf, Far East,
  US Gulf, US Atlantic, West Africa), and `normalise_port()` already
  degrades gracefully to `None` for anything unresolvable rather than
  guessing.
- PRD §2.1 states actual volume: **20–60 inbound cargo emails/day**, one
  broker mailbox (no multi-broker support built or scoped yet — PRD §11.2's
  separate "Multi-broker configuration" row is its own undesigned Low item).

## Decision

### 1. Do not build the export mechanism now — no endpoint, no job, no schema, no code

No `outbound`-side export code is authorized by this ADR. `coder` builds
nothing as a result of this document. This mirrors ADR 0005's "Nothing to
build" outcome, but for a different reason: ADR 0005 resolved *existing*
behavior as already adequate; here there is no existing behavior to
evaluate — this is a request for net-new, speculative infrastructure with
zero real consumer, zero contract, and zero stated requirements (what
fields, what cadence, what price, what access model a bank or hedge fund
actually wants). Building against a one-sentence placeholder risks
building the wrong thing and then re-doing it once a real buyer's actual
requirements surface — the small stated estimate ("2 weeks," "Low"
priority) does not change that calculus; effort-to-build is not the same
question as value-of-building-it-now, and per this project's established
discipline (ADR 0005) declining speculative work absent concrete signal is
the default, not the exception.

### 2. Record the safe target-state anonymisation/aggregation design now, as a guardrail — not as a build authorization

Unlike ADR 0005's questions (UI responsiveness, batching), this feature's
central risk is confidentiality: `charterer` directly identifies a
counterparty in a specific deal, and `freight_rate` is simultaneously the
single most commercially valuable field to this feature's stated buyer
persona *and* one of the most re-identifying (see field classification
below). That combination is high-stakes enough to be worth writing down
now, cheaply, so that if this feature is ever picked up under commercial
pressure later, whoever builds it inherits an explicit boundary instead of
re-deriving one under time pressure (or missing it entirely). This is the
"document the design, build nothing" middle ground: real design reasoning,
zero implementation, zero schema, zero CLAUDE.md update.

#### Field classification

**Stripped entirely — never appear in any export, at any aggregation
level:**

| Field | Table | Why |
|---|---|---|
| `charterer` | InboundOrder | Directly identifies a specific counterparty in a specific deal. This is the clearest "must never leave the system" field in the schema — ordinary broker-client confidentiality, not just NFR compliance. |
| `sender`, `subject`, `raw_body` | InboundOrder | Free-text identity/content; no aggregation makes these safe. |
| `message_id`, `in_reply_to`, `references`, `thread_id`, `source_message_id` | InboundOrder | Threading/dedup plumbing, not market data; also potentially correlatable back to a specific mailbox/message. |
| `confidence_scores`, `parse_confidence`, `has_low_confidence_fields`, `low_confidence_field_names`, `parse_error`, `is_read` | InboundOrder | Internal parse-quality/UI-state metadata — not market data, no legitimate reason for an external consumer to see it, no privacy upside to including it either. |
| `id` (order UUID) | InboundOrder | No per-row identifier of any kind should survive into an aggregate export — the whole point is that no output row corresponds to a single order. |
| `vessel_id` (IMO), `vessel_name` | MatchResult | Ties a specific named vessel to a specific order/charterer — exactly the kind of link that makes an aggregate re-identifiable, independent of whether the charterer's name is attached. Also a likely licensing concern: vessel positions are Signal Ocean's own commercial data, not this project's to resell — flagged, not resolved, here (see Open questions). |
| `open_port`, `open_date`, `rank`, and the four `score_*` columns | MatchResult | Describe *our matching engine's output*, not the market — irrelevant to an "order flow" product and out of scope regardless of anonymisation. |
| Entire `OutboundMessage` table | — | Reply correspondence content, not order/market flow data at all. Explicitly out of scope — confirming this by name rather than silently omitting it, per this task's ask to state it explicitly rather than assume it away. |

**Generalised/bucketed — only a coarsened form ever appears:**

| Field | Table | Generalisation |
|---|---|---|
| `load_port`/`load_port_canonical`, `discharge_port`/`discharge_port_canonical` | InboundOrder | Reduce to `core/ports.py`'s existing `PORT_TO_AREA` area (10 areas) — never the raw port. Ports `normalise_port()` can't resolve fall into an explicit `"Other/Unresolved"` area bucket rather than being passed through raw or silently dropped (dropping would bias the aggregate; passing raw text risks a rare/unaliased berth name acting as a near-unique identifier). |
| `quantity_mt`/`quantity_min_mt`/`quantity_max_mt`, `vessel_size_dwt` | InboundOrder | Size band, not exact tonnage — reuse the existing tanker/bulk class boundaries already implicit in `matching/engine.py` (e.g. Handysize/Handymax/Panamax/Aframax/Suezmax/VLCC-equivalent MT bands) rather than inventing a new banding scheme. |
| `laycan_start`/`laycan_end` | InboundOrder | Not carried as dates at all — the export's own time-bucket (see aggregation level below) *is* the temporal generalisation; no per-order date survives. |
| `cargo_type` | InboundOrder | Currently free text (confirmed above — "crude oil", "fuel oil", "grain", "wheat", "corn", ... are distinct strings today). Passing this through raw would fragment buckets and reintroduce small-count re-identification risk (a rare exact cargo string could itself be a quasi-identifier). Needs mapping to a small fixed category list (e.g. Crude/Dirty Products, Clean Products, LPG/LNG, Grain/Dry Bulk, Other) before export — this mapping is itself a small piece of design work not yet done, flagged as a prerequisite, not solved here. |
| `vessel_type` | InboundOrder (requested) / `vessel_class` (MatchResult) | Already reasonably coarse (Aframax/Suezmax/etc.) — usable as a groupby dimension largely as-is, contingent on the same free-text-fragmentation check as `cargo_type`. |

**Excluded for now, pending further work — not classified as safe or
unsafe, just not ready:**

| Field | Why |
|---|---|
| `freight_rate` | Two independent, compounding reasons this cannot be exported yet, either one sufficient on its own: **(a) It isn't a usable numeric field today** — `Column(String(100))`, free text, mixed units by trade (Worldscale points, $/mt lumpsum, flat lumpsum). No aggregation ("average," "bucket into a range") is possible on data that isn't parsed into comparable numeric units first — that's real parser-normalisation work, not export-job work, and isn't scoped by the PRD's "2 weeks" (which reads as an export-job estimate, not a parsing-rework estimate). **(b) Even normalised, it is the highest re-identification-risk field in the schema, for a reason specific to this feature's own stated buyer**: a specific fixture rate for a specific route/size/date is frequently independently discoverable (Baltic Exchange reports, broker market circulars) by market participants who watch rates closely — which is exactly the persona this product targets. A sophisticated bank/hedge-fund consumer could plausibly reverse-engineer which specific fixture an aggregate cell describes by cross-referencing a narrow rate figure against public market chatter, even with `charterer` stripped and geography/size bucketed. This elevates `freight_rate`'s risk above a naive k-anonymity count-threshold model, because the threat model here specifically includes an analytically capable adversary, not just a casual observer. If ever revisited, `freight_rate` would need: normalisation into a real numeric+unit field first (separate ADR/effort), export only as a bucketed range or period average (never a fixture-level figure), a stricter minimum-count threshold than other fields, and — flagged explicitly as outside this ADR's competence — probable legal/compliance review, since fixture-rate confidentiality is a matter of broker-client contractual practice as much as a technical design question. |

**Safe as-is, at the aggregate level only:** counts (orders per bucket),
the generalised `cargo_type` category, `vessel_type`/`vessel_class`
category, the generalised geographic area, the size band, and the time
bucket itself.

#### Aggregation level — reasoned from actual stated volume, not hand-waved

PRD §2.1: 20–60 inbound orders/day, one broker mailbox. `core/ports.py`
already fixes the geography dimension at 10 areas; a realistic cargo-type
taxonomy (per above) is roughly 5–6 categories; a realistic size-band
taxonomy is roughly 5–6 bands. Even restricting to *load area* alone
(ignoring discharge area, which would multiply this further), the
cross-product of (area × cargo category × size band) is on the order of
150–360 possible buckets, and real trade-lane concentration (a desk
specialising in, say, 20–30 "popular" combinations) doesn't eliminate the
long tail — it just moves where it lands.

- **Daily aggregation is not safe.** 20–60 orders spread across 150+
  possible buckets means the expected count per bucket per day is
  overwhelmingly 0 or 1. A bucket with exactly 1 underlying order is not
  meaningfully different from publishing that order's own bucketed fields
  directly — it's re-identification by construction, not in some edge
  case.
- **Weekly aggregation (140–420 orders/week) is still not comfortably
  safe.** Even concentrating on ~20–30 "popular" lane/cargo/size
  combinations, that's roughly 5–14 orders/week per popular combination —
  borderline for a k≥5 threshold on the popular lanes, and still routinely
  1–2 (or zero) on anything in the long tail, which won't stop existing
  just because it's aggregated weekly.
- **Monthly aggregation (600–2,400 orders/month)** gets the popular
  combinations comfortably into k≥5+ territory (roughly 20–80/month each)
  but **does not, by itself, solve the long tail** — an unusual
  cargo/route/size combination that occurs once a month is still exactly
  one order, however wide the time window.

**Conclusion: monthly is the minimum safe default period, and a
minimum-count threshold (suggest k≥5, consistent with common
k-anonymity practice) must be applied on top of monthly aggregation, not
instead of it** — the two are complementary levers, not substitutes.
Buckets below the threshold should be **rolled up into a coarser
geographic or cargo grouping** (e.g. merge two under-populated areas into
a broader region, or fold an unusual cargo type into "Other") in
preference to outright suppression where possible, since suppression
alone silently discards signal about genuinely-occurring-but-rare trade
activity that a coarser-but-still-meaningful bucket could still convey;
where no reasonable rollup gets a bucket over threshold, it is suppressed
(omitted from that month's export) rather than published below-threshold.

### 3. If ever built, this should be a periodic export job, not a live API

The PRD's own phrase — "Aggregated anonymised order flow **API**" —
is the part of the placeholder most worth pushing back on. An API implies
a live listener a bank or hedge fund's system calls into, which requires:

- An actual inbound network exposure reachable by an external party — by
  definition not localhost, which is the entire premise NFR-07 protects
  today. Nothing else in this architecture has ever needed to accept
  connections from off-machine; this would be a first, and a categorically
  different deployment topology (public-facing hosting, TLS termination,
  uptime expectations) than "single-broker-desk localhost tool."
- An authentication/authorization model with zero specified requirements
  today — no known consumer to scope credentials for, no multi-tenancy
  need established (there is exactly one hypothetical buyer type
  mentioned, not even one named buyer).

**If this is ever built, the better shape is a periodic export job**
(reusing this codebase's existing `scheduler/jobs.py`
APScheduler pattern) that computes the monthly aggregate and writes it to
a file — Parquet/CSV to local disk, or an object-store location supplied
by whoever the actual buyer relationship turns out to be (S3 bucket, SFTP
drop, even a manually-reviewed monthly file handed over through whatever
channel a real contract specifies). This is a **push**, not a **pull**:
nothing about it requires this backend to ever accept an inbound
connection from an external party, so NFR-07 stays fully intact regardless
of whether this job is ever built. It also correctly defers the
authentication question entirely — there is no "who authenticates against
this" problem for a job that writes outward to a destination a real
contract will specify, versus a service this backend must serve requests
from indefinitely for tenants that don't exist yet.

### 4. NFR exception — prospective, not active

No NFR-07 or NFR-09 exception is being invoked *today*, because nothing
this ADR authorizes writes any code — there is no live violation to
record, unlike ADR 0004 which was actively building the SMTP path in the
same document. What this ADR does record, for whoever eventually builds
the export job:

- **NFR-07 is not expected to require any exception, ever, under the
  design in Decision #3.** A push-style export job that writes outward
  never opens an inbound listener; "no inbound ports exposed to the
  network" remains true regardless of whether this feature ships, as long
  as the API shape rejected in Decision #3 stays rejected. If a future
  ADR ever *does* choose a live pull-style API instead, that reversal
  would need its own explicit NFR-07 exception recorded at that time,
  symmetric to how this document treats NFR-09 below — it is not
  pre-authorized here.
- **NFR-09 will need an explicit, scoped exception at build time**,
  symmetric to ADR 0004 Decision #5's SMTP precedent: the export job's
  egress step (an S3 upload, an SFTP send, however delivery is actually
  implemented) is a deliberate point where data leaves the local machine
  for a reason other than Anthropic parsing. Recorded now so it reads as
  anticipated rather than discovered later: **the only data permitted to
  cross that boundary is the aggregated, generalised, threshold-cleared
  output of Decision #2** — never a raw `InboundOrder`/`MatchResult` row,
  never `charterer`, never an un-normalised `freight_rate`, regardless of
  how commercially tempting a richer export might be under future
  pressure from an actual paying buyer. Any future relaxation of that
  boundary (e.g. a buyer wanting fixture-level data under a private data
  license) is a materially different, more sensitive product decision
  that would need its own ADR and, per Decision #2's `freight_rate` entry,
  real legal/compliance input — not something to slide into under the
  cover of this document.

## Options considered

### Whether to build the export mechanism now

| Option | Description | Outcome |
|---|---|---|
| **A. Build the full export (API + schema + job) now** | Implement PRD §11.2's row as literally described. | Rejected. No real consumer, no real requirements (fields/cadence/price/access model all unknown), and the PRD's own "API" framing collides directly with NFR-07 in a way that would need to be solved speculatively for tenants that don't exist. Building this now risks building the wrong thing and re-doing it once real requirements surface. |
| **B. Hold off entirely — no design, no ADR, nothing recorded** | Defer the whole question, including any documentation of it, until a real buyer exists. | Rejected. This feature's specific risk profile (`charterer`, `freight_rate`) is high-stakes enough that leaving *zero* guardrail on record is itself a risk — a future task picking this up under commercial pressure, with no context, could plausibly ship something that leaks confidential counterparty/rate data before anyone catches it. The cost of writing the guardrail down now is low (a design document, no code); the cost of it being missing later, at the wrong moment, is not. |
| **C. Document the safe target-state design now (field classification, aggregation level, access-model direction, NFR treatment); build no code (chosen)** | This ADR. | **Accepted.** Matches the task's own framing of a legitimate middle ground: real design reasoning captured while it's cheap, zero speculative implementation, explicit revisit trigger tied to a real buyer appearing. Consistent with this project's own stated architect responsibility to record non-trivial design decisions before they're needed, not only after. |

### API vs. periodic export job (only relevant if/when Decision #1 is revisited)

| Option | Description | Outcome |
|---|---|---|
| **A. Live `GET /analytics/...` API, as PRD §11.2 literally describes** | A queryable endpoint an external consumer calls. | Rejected for now. Requires an inbound port reachable off-machine (violates NFR-07 as currently written), and an auth/multi-tenancy model with no specified requirements. Would be reconsidered only if a real buyer relationship specifically needs on-demand querying rather than a periodic drop — not established today. |
| **B. Periodic export job writing to a file/object-store destination (recommended direction if ever built)** | Reuses the existing `scheduler/jobs.py` APScheduler pattern; writes a monthly aggregate; delivery destination determined by whatever a real contract specifies. | Preferred direction, not yet authorized to build. Sidesteps the auth-model problem entirely (no listener, so no "who authenticates" question) and keeps NFR-07 intact by construction. |

## Reasoning

- **The task's own framing — "there is no way to build a real analytics
  export feature without touching NFR-07/09 in some way" — is correct,
  but the resolution is to choose a shape that minimizes and scopes the
  touch, not to accept the PRD's literal "API" framing as fixed.** Once
  "periodic push job" is chosen over "live pull API," NFR-07 stops being
  implicated at all, and NFR-09 is implicated only at one clearly-bounded
  egress point — exactly the kind of narrow, explicit, scoped exception
  ADR 0004 modeled for SMTP send, rather than a broad, ongoing exposure.
- **`charterer` and `freight_rate` deserve the asymmetric treatment they
  get here precisely because they fail differently.** `charterer` is
  unsafe at *any* aggregation level in any form other than being counted —
  there's no bucketing of a counterparty name that makes it safe, so it's
  simply excluded, full stop. `freight_rate` is different: it's not
  unsafe by nature (a wide range, or a well-thresholded route/month
  average, could plausibly be safe) — it's unsafe *today* because (a) the
  underlying data isn't even normalised into comparable units yet, an
  engineering gap independent of privacy, and (b) its re-identification
  risk is elevated specifically by the sophistication of this feature's
  own target buyer, which is a subtler argument than a generic
  k-anonymity count and worth stating explicitly rather than folding into
  the same bucket as `charterer`.
- **The volume math is the crux of the aggregation-level decision, and
  hand-waving it would have produced the wrong answer.** "Daily" sounds
  like the obvious default granularity for an "order flow" product, but
  at 20–60 orders/day spread across 150+ realistic bucket combinations,
  daily aggregation is barely different from publishing individual
  bucketed orders — it only looks safe if you don't multiply out the
  dimensions against the actual stated volume. Monthly-plus-threshold is
  the conclusion that survives actually doing that arithmetic, not an
  assumed conservative default.
- **This project's established discipline (ADR 0005) of declining
  speculative infrastructure absent concrete signal does hold here, and
  applying it faithfully means distinguishing "build nothing" from
  "record nothing."** ADR 0005 correctly built nothing because the
  underlying behavior was already adequate. Here, nothing exists yet to
  evaluate — but unlike ADR 0005's UI-responsiveness questions, this
  feature's central risk (confidential counterparty/rate data) is
  severe enough that writing down the boundary now, at near-zero cost,
  is a better application of that same discipline than treating "no
  code" and "no record" as the same decision. The task's own framing
  anticipated exactly this distinction by floating the design-only middle
  ground explicitly.

## Consequences

### To build (handoff to Agent 3 — Coder)

**Nothing.** This ADR authorizes zero code, zero schema changes, zero new
endpoints, zero new scheduler jobs. It is a design record only.

### To retire

Nothing. No existing code path is affected.

### Unchanged (explicit scope boundary)

- `middleware/database/models.py` — no new tables/columns. Nothing here
  is exported today or as a result of this ADR.
- `middleware/core/ports.py` — its existing `PORT_TO_AREA` taxonomy is
  identified here as reusable for a future export's geographic
  generalisation, but is not modified.
- `middleware/scheduler/jobs.py` — identified as the pattern a future
  export job would reuse, not touched here.
- CLAUDE.md — not updated by this ADR. Per the ADR-before-CLAUDE.md
  sequencing established by ADR 0001–0005, and doubly so here since there
  is no implementation for CLAUDE.md to eventually describe.
- The PRD document itself — not edited. Per the same constraint ADR 0004
  operated under, any corrected/narrowed language (here: "API" →
  "periodic export job," per Decision #3) lives in this ADR only.

## Open questions (not decided here)

1. **`cargo_type` and `vessel_type`/`vessel_class` category mapping.**
   This ADR establishes that free-text values must be mapped to a small
   fixed category list before export, but does not define that list. Small
   piece of follow-on design work if this feature is ever picked up —
   route to `architect` again at that point.
2. **Vessel-position data licensing.** `MatchResult`'s vessel snapshot
   (`vessel_id`/IMO, `open_port`, `open_date`) is sourced from Signal
   Ocean's tonnage list. Even though this ADR excludes these fields from
   any order-flow export on confidentiality grounds (Decision #2), there's
   a separate, unresolved question of whether Signal Ocean's own
   commercial terms would permit re-exporting any vessel-position-derived
   data in a paid product at all. Not an architecture question — flagged
   for whoever owns the Signal Ocean commercial relationship, not decided
   here.
3. **`freight_rate` normalisation.** If this field is ever wanted in a
   future version of this export, normalising it into a real
   numeric-plus-unit field is a prerequisite (see Decision #2) and would
   need its own scoping — likely touching `parser/llm_parser.py` and
   `database/models.py`, not just the export path. Not scoped here.
4. **What "a real buyer" needs to look like before Decision #1 is
   revisited.** This ADR's implicit revisit trigger is a signed contract
   or at minimum a concrete LOI specifying actual fields/cadence/price/
   access model — not merely renewed interest in the PRD line item.
   Whoever revisits this should re-validate the field classification and
   aggregation level against that buyer's actual stated requirements
   rather than assuming this ADR's design is still the right shape
   unchanged — a real buyer may want something this design doesn't
   anticipate (e.g. per-vessel-class subscription tiers, a different
   geographic granularity than `core/ports.py`'s existing areas).

## References

- `.claude/decisions/0004-smtp-send-and-wt3-clone-email-client.md` —
  Decision #2 (correcting a PRD non-goal without editing the PRD itself)
  and Decision #5's "Security / NFR exception" subsection are the direct
  precedents this ADR follows for narrowing PRD language and recording a
  deliberate, scoped NFR exception rather than a silent violation.
- `.claude/decisions/0005-broadcast-batching-and-reconnect-catchup.md` —
  precedent for this project's discipline of declining speculative
  infrastructure absent concrete signal; this ADR applies the same
  discipline to the build-vs-wait question while distinguishing "build
  nothing" from "record nothing," given this feature's materially higher
  confidentiality stakes.
- `docs/maritime_middleware_prd_v1.1.pdf` §11.2 ("Analytics data export"
  row), §2.1 (stated inbound volume, 20–60 orders/day), §1.4 (Non-Goals),
  NFR-07, NFR-09 — the sections this ADR scopes against.
- `middleware/database/models.py`, `middleware/core/ports.py`,
  `middleware/matching/engine.py` — current schema and existing
  geographic/cargo taxonomies verified directly for this ADR's field
  classification and aggregation-level reasoning.
