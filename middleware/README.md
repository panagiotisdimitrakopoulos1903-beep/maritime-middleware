# Maritime Middleware — Python Backend

End-to-end Python backend for the WT3 → Signal Ocean matching middleware.

## Architecture

```
milter/hook.py          Mail server interception (Postfix Milter)
parser/llm_parser.py    LLM-based structured field extraction (Claude)
core/ports.py           Port name normalisation and geographic scoring
signal_client/client.py Signal Ocean API wrapper + local cache
matching/engine.py      Vessel scoring and ranking engine
scheduler/jobs.py       Background Signal cache refresh (APScheduler)
api/app.py              FastAPI REST + WebSocket server
database/models.py      SQLAlchemy models (PostgreSQL)
```

## Setup

### 1. Prerequisites

- Python 3.11+
- PostgreSQL 14+
- Postfix mail server (for Milter) or Exchange (requires C# Transport Agent instead)
- Signal Ocean API subscription — https://apis.signalocean.com
- Anthropic API key — https://console.anthropic.com

### 2. Install dependencies

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env with your API keys and database URL
```

### 4. Create database

```bash
# Create PostgreSQL database
psql -U postgres -c "CREATE USER middleware WITH PASSWORD 'middleware';"
psql -U postgres -c "CREATE DATABASE maritime_middleware OWNER middleware;"

# Tables are created automatically on first startup
```

### 5. Run the backend

```bash
# Terminal 1 — FastAPI server
uvicorn main:app --host 127.0.0.1 --port 5000

# Terminal 2 — Milter (mail interception)
python -m milter.hook
```

### 6. Run tests

```bash
pytest tests/ -v
```

## Key API endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/internal/ingest` | Called by Milter with raw email |
| GET | `/matches/{order_id}` | Ranked matches for an order |
| GET | `/orders/latest` | Recent orders with top match |
| GET | `/status` | System health and cache age |
| WS | `/ws` | WebSocket push to Electron panel |

## Scoring weights

Configurable in `.env`:

```
WEIGHT_VESSEL_SIZE=0.35
WEIGHT_GEOGRAPHY=0.30
WEIGHT_DATE_OVERLAP=0.20
WEIGHT_CARGO_TYPE=0.15
```

## Milter configuration (Postfix)

Add to `/etc/postfix/main.cf`:

```
smtpd_milters = inet:localhost:9900
milter_default_action = accept
milter_protocol = 6
```

Then restart Postfix:
```bash
sudo systemctl restart postfix
```
