# ADR 0001: Ingestion via IMAP MCP (retire Milter)

**Status:** Accepted  
**Date:** 2026-07-12  
**Deciders:** User (project owner)

## Context

The middleware needs a way to get inbound shipping orders from Telix WT3
into the parsing and matching pipeline. Two ingestion paths were built:

1. **Postfix Milter** (`middleware/milter/hook.py`) — intercepts mail
   server-side before delivery to WT3.
2. **AutoHotkey WT3 watcher** (`middleware_trigger/`) — watches the WT3
   Windows client and POSTs clipboard content to `POST /internal/ingest`.

The original project pitch proposed a different model: Claude reads the WT3
mailbox directly via an **IMAP MCP server**, sitting between the mailbox and
Signal Ocean data.

The built system diverged from that pitch. This ADR records the decision to
**abandon Milter** and **adopt IMAP MCP** as the target ingestion architecture.

## Decision

1. **Retire Postfix Milter** as an ingestion path. Do not deploy, extend, or
   depend on `middleware/milter/hook.py` for new work.
2. **Adopt IMAP MCP** as the target ingestion model: Telix WT3's mailbox is
   accessed via IMAP; Claude connects to that mailbox through an IMAP MCP
   server to read and parse WT3 messages.
3. **Keep the existing processing pipeline** (`parse → match → persist →
   push`) in `middleware/` for now. Matching, Signal Ocean cache, database,
   and the Electron companion panel remain unchanged until a follow-on ADR
   says otherwise.
4. **Treat AutoHotkey trigger as transitional.** It may remain as a fallback
   during migration but is not the long-term ingestion design.

## Options considered

| Option | Description | Outcome |
|--------|-------------|---------|
| **A. Keep Milter** | Server-side mail interception via Postfix | Rejected — abandoned per user direction |
| **B. Keep AHK only** | Clipboard watcher as sole client-side path | Rejected as long-term — superseded by IMAP MCP |
| **C. IMAP MCP (chosen)** | Claude reads WT3 mailbox via IMAP MCP server | Accepted — aligns with original pitch |
| **D. IMAP poller → REST** | Background service fetches IMAP, POSTs to `/internal/ingest` | Not chosen yet — see open questions |

## Reasoning

- **Milter is operationally heavy.** It requires controlling the broker's
  Postfix mail flow, a separate daemon process, and `pymilter` — none of
  which fit a WT3-centric deployment where the mailbox is the source of
  truth.
- **IMAP MCP matches how WT3 actually works.** WT3 is an email/telex client
  with a mailbox Claude can access via standard IMAP, rather than a protocol
  the backend must intercept mid-delivery.
- **Preserves working downstream code.** The parser, matching engine, Signal
  Ocean cache, database, and Electron panel are functional. Only the
  ingestion boundary changes.
- **Moves toward the original vision incrementally.** This is a deliberate
  step toward MCP-based ingestion without requiring an immediate full
  agentic rewrite or WT3 UI clone.

## Consequences

### To build

- **IMAP MCP server** — exposes mailbox tools (list, fetch, search) for
  Claude to read WT3 messages.
- **WT3 message identification** — filter shipping orders from mailbox noise.
- **Integration boundary** — how parsed orders reach the matching engine
  (see open questions).

### To retire (when implementation is ready)

- `middleware/milter/` and `pymilter` dependency
- Milter config in `config.py`, `.env.example`, READMEs
- Milter references in agent definitions and `CLAUDE.md`

### Unchanged (for now)

- `middleware/parser/llm_parser.py` — may coexist with or be replaced by
  agentic Claude parsing; not decided here
- `middleware/matching/engine.py` — scoring and ranking
- `middleware/signal_client/client.py` — direct SDK, not Signal Ocean MCP
- `middleware_frontend/` — companion panel beside WT3
- `POST /internal/ingest` — may remain as an internal handoff endpoint

## Open questions (not decided in this ADR)

1. **Agentic vs pipeline handoff.** Does Claude parse and orchestrate the
   full loop via MCP tools (read → parse → match → respond), or does an
   IMAP-connected service still POST raw messages to `/internal/ingest` and
   let `llm_parser.py` do the one-shot extraction?
2. **AHK trigger fate.** Retire entirely once IMAP MCP is production-ready,
   or keep as offline/fallback?
3. **Signal Ocean MCP.** Original plan included a second MCP server. Current
   code uses direct SDK calls with a Postgres cache. No change decided here.
4. **Email client scope.** Companion panel stays; no decision yet on building
   a fuller email-client UI.

## References

- `CLAUDE.md` — project memory (updated 2026-07-12 to reflect this ADR)
- `.claude/agents/architect.md` — design authority for follow-on decisions
