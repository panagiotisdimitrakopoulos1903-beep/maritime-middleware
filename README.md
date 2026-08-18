# Maritime Middleware

**A real-time order-matching platform for maritime chartering brokers**, built to close the gap between a legacy broker inbox (Telix WT3) and a market intelligence platform (Signal Ocean) — two systems with no way to talk to each other.

Proposed, designed, and independently delivered for HellasChart Ltd.

---

## The problem

A shipping broker receives dozens of inbound cargo order emails per day. For each one, the broker manually cross-references a market intelligence platform to find available vessels matching the cargo type, quantity, ports, and laycan dates. This takes 10–15 minutes per order and doesn't scale during busy periods — orders sit unmatched, and fixtures get missed.

The broker's inbox tool, Telix WT3, has no public API. There was no vendor-supported way to automate this.

## The solution

Maritime Middleware watches the broker's mailbox, parses each inbound order using an LLM, scores it against live vessel availability across four weighted dimensions, and surfaces ranked matches inside a companion email client — in real time, with no change to how the broker actually works. They open an email; the matches are already there.

```
Telix WT3 ──▶ IMAP Mailbox ──▶ IMAP MCP Server ──▶ Claude (parsing)
                                                          │
                                              Signal Ocean API ──┐
                                                                  ▼
                                                          Python Backend
                                                       (matching engine, DB)
                                                          │         │
                                                   PostgreSQL   WebSocket
                                                                     │
                                                                     ▼
                                                          Electron Panel (UI)
```

No GUI automation, no mail-server plugin — ingestion and delivery both run over standard mail protocols (IMAP/SMTP), which is the only integration surface a legacy, API-less tool like Telix WT3 actually exposes.

## Key features

- **Real-time ingestion** — a background poller watches the mailbox and feeds new orders into the pipeline within seconds of arrival
- **LLM-based parsing** — extracts cargo type, quantity, ports, and laycan dates from free-text broker shorthand (e.g. `"55k grain ant/jpn lc aug 10-20"`), with per-field confidence scoring
- **Weighted vessel matching** — scores candidate vessels across size, geography, laycan overlap, and cargo compatibility; configurable weights, sub-100ms match latency against a local cache
- **Full email-client capability** — threaded replies (RFC 2822 compliant), SMTP send, inbox/sent folders, read/unread state — not just a read-only dashboard
- **Fails safely, not silently** — every external dependency (mail server, market data API, LLM) can fail without corrupting state or hiding the failure from the operator

## Architecture

| Layer | Responsibility |
|---|---|
| **Mail Ingestion** | IMAP polling — reads the broker's mailbox, no changes to Telix WT3 |
| **Python Backend** | FastAPI — parsing orchestration, matching engine, WebSocket push |
| **Database** | PostgreSQL, versioned via Alembic — orders, matches, sent messages |
| **Email Client UI** | Electron + React — real-time order list, match panel, compose/reply |
| **Agent System** | Claude Code, 5 specialized agents — architecture, implementation, QA, reporting, coordination |

Two [Model Context Protocol](https://modelcontextprotocol.io) servers expose the mailbox and market data as structured tools for interactive/agentic use; the production ingestion path runs independently as a scheduled backend job, not an agentic loop — a deliberate reliability decision documented in [`ADR 0002`](.claude/decisions/).

## Engineering process

Every non-trivial architectural or scope decision is documented as an Architecture Decision Record before implementation — 7 ADRs across this project, covering everything from the core ingestion strategy to a UX gap caught by manual testing after full automated test coverage had already passed. See [`.claude/decisions/`](.claude/decisions/).

A few of the more interesting ones:

- **[ADR 0002](.claude/decisions/)** — chose a scheduled polling job over an agentic MCP loop for production ingestion, since MCP servers are host-driven and require a live session — a reliability regression for something that needs to run unattended
- **[ADR 0003](.claude/decisions/)** — fixed a cross-thread concurrency bug where WebSocket notifications silently failed to deliver under Python's asyncio threading model, and eliminated a related unguarded race condition in the same redesign
- **[ADR 0007](.claude/decisions/)** — closed a data-integrity gap where a failed parse produced a match score visually indistinguishable from a genuine result, caught by manual inspection of the running app after 100+ automated tests had already passed

A second, independent bug — a cache-refresh job silently logging success while deleting real cached data on partial API failure — was found via the project's own daily-briefing agent and fixed the same day.

## Stack

**Backend:** Python, FastAPI, SQLAlchemy, Alembic, PostgreSQL, APScheduler
**Frontend:** Electron, React
**AI/Integration:** Anthropic Claude (parsing), Model Context Protocol, Signal Ocean SDK
**Testing:** pytest (100+ tests), Jest

## Status

Fully built and tested end-to-end against a simulated (mock IMAP/mail + mock market data) environment. Production rollout is pending final credential provisioning from the broker's IT team and the market data vendor — the entire system runs identically once those are supplied, by design.

---

*Built independently, June 2026–present.*
