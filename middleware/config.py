"""
config.py — centralised settings loaded from environment / .env file
"""
from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional


class Settings(BaseSettings):
    # ── Application ──────────────────────────────────────────────────────────
    app_name: str = "Maritime Middleware"
    debug: bool = False
    host: str = "127.0.0.1"
    port: int = 5000

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql://middleware:middleware@localhost:5432/maritime_middleware",
        description="PostgreSQL connection string"
    )

    # ── Anthropic / LLM ──────────────────────────────────────────────────────
    anthropic_api_key: str = Field(..., description="Anthropic API key for Claude")
    llm_model: str = "claude-sonnet-4-6"
    llm_max_tokens: int = 1024
    parser_confidence_threshold: float = 0.75  # flag fields below this

    # ── Signal Ocean ─────────────────────────────────────────────────────────
    signal_ocean_api_key: str = Field(..., description="Signal Ocean API key")
    signal_cache_refresh_minutes: int = 5
    signal_laycan_window_days: int = 30      # how far ahead to fetch tonnage
    signal_max_results: int = 50             # vessels per cache refresh

    # ── IMAP ingestion ───────────────────────────────────────────────────────
    # IMAP_MODE itself stays a bare os.environ read (see scheduler/jobs.py and
    # mcp/imap_server.py) so the MCP server remains independently startable
    # without pulling in the full pydantic-settings config chain.
    imap_poll_interval_minutes: int = 2
    imap_poll_batch_size: int = 20

    # Only required when IMAP_MODE=live (mcp/imap_client.py). Optional with
    # no default so that IMAP_MODE=mock (the default, and what local dev /
    # seed data relies on per CLAUDE.md) never forces a developer to set
    # real or dummy IMAP credentials just to start the backend — Settings()
    # is constructed unconditionally at import time in api/app.py regardless
    # of IMAP_MODE.
    imap_host: Optional[str] = None
    imap_port: Optional[int] = None
    imap_user: Optional[str] = None
    imap_pass: Optional[str] = None

    # ── Milter (mail interception) ────────────────────────────────────────────
    milter_socket: str = "inet:9900@localhost"
    milter_name: str = "MaritimeMilter"

    # ── Matching ─────────────────────────────────────────────────────────────
    match_top_n: int = 5                     # how many ranked results to return

    # Scoring weights — must sum to 1.0
    weight_vessel_size: float = 0.35
    weight_geography: float = 0.30
    weight_date_overlap: float = 0.20
    weight_cargo_type: float = 0.15

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
