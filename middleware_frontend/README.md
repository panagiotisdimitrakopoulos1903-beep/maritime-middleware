# Maritime Panel — Electron + React Frontend (Layer 4)

Floating always-on-top desktop panel for Windows.
Docks to the right side of the screen alongside WT3.
Connects to the Python backend on `localhost:5000`.

## What it does

- Shows all inbound orders in a scrollable left pane
- When an order is clicked, instantly displays ranked vessel matches
- New orders flash with an amber dot when they arrive via WebSocket push
- Low-confidence parsed fields are highlighted amber
- Score breakdown bar shows size / geography / date / cargo contributions
- Cache age shown so broker knows how fresh the Signal data is

## Prerequisites

- Node.js 18+
- Python backend (Layer 2) running on `localhost:5000`
- Database (Layer 3) seeded with test data

## Setup

```bash
npm install
```

## Development (React + Electron together)

```bash
npm start
```

This starts the React dev server on port 3000, then launches Electron
pointing at it. Hot reload works — save a file and the panel updates instantly.

## Production build

```bash
npm run dist
```

Produces a Windows installer in `dist/`. One-click install, no Node.js required
on the broker's machine.

## Ingestion

No manual step is needed for messages to arrive. The backend polls the WT3
mailbox itself via the IMAP MCP poller (ADR 0002) and pushes new matches to
the panel over WebSocket as soon as they're ready.

## Layout

```
┌──────────────────────────────────────────────┐  420px wide
│  ⚓ MARITIME  ● 14 vessels  2m ago      —    │  StatusBar (36px)
├──────────────┬───────────────────────────────┤
│  INBOUND  30 │  MATCH RESULTS                │
│              │  Parsed order        96% conf  │
│  ● Grain     │  Cargo: Grain                  │
│    55k MT    │  Qty:   55,000 MT              │
│    ANT→JPN   │  Load:  Antwerp                │
│    2m ago    │  Disch: Japan (any)            │
│  ─────────── │  Laycan: 10 – 20 Aug           │
│  Coal        │                               │
│    75k MT    │  Top 5 vessels                 │
│    RTM→KOR   │  ┌─ 1 ─────────────────────┐  │
│    20m ago   │  │ MV Aegean Harvest    94  │  │
│              │  │ Panamax · 76k DWT         │  │
│              │  │ Open: Amsterdam, 5 Aug    │  │
│              │  │ ████░░░░ Size·Geo·Date   │  │
│              │  └───────────────────────────┘  │
│              │  ┌─ 2 ─────────────────────┐  │
│              │  │ MV Baltic Trader     88  │  │
│              │  └───────────────────────────┘  │
└──────────────┴───────────────────────────────┘
```

## File structure

```
electron/
  main.js          Electron main process — window creation, IPC
  preload.js       Secure context bridge to renderer

src/
  App.jsx          Root — layout, WebSocket, state
  index.js         React entry point
  index.css        Design tokens, global styles

  components/
    StatusBar.jsx        Top bar — connection, cache age, minimise
    OrderList.jsx        Left pane — scrollable order list
    OrderCard.jsx        Single order row
    MatchPanel.jsx       Right pane — parsed fields + ranked deals
    ParsedOrderSummary.jsx  Extracted fields with confidence flags
    DealCard.jsx         Single ranked vessel card
    ScoreBar.jsx         Segmented score breakdown bar

  lib/
    api.js          HTTP + WebSocket client for Python backend
```
