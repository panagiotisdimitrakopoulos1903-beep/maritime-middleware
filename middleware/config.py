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

    # ── SMTP outbound send ───────────────────────────────────────────────────
    # ADR 0004, Decision #5. Deliberately separate fields from imap_* above
    # (not reused/aliased) even though in practice they'll usually hold the
    # same broker mailbox credentials — different hostnames on the same
    # provider are common (e.g. imap.brokerfirm.com vs smtp.brokerfirm.com),
    # and collapsing them would mean an IMAP credential rotation silently
    # changes SMTP behavior too. Optional with no default for the same reason
    # as imap_host/port/user/pass: Settings() is constructed unconditionally
    # at import time, and local dev / tests must not require real (or dummy)
    # SMTP credentials just to start the backend.
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_pass: Optional[str] = None
    smtp_use_tls: bool = True

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
        extra = "ignore"  # SIGNAL_MODE / IMAP_MODE live in .env but are read
                          # directly via os.environ (see comments above), not
                          # declared as Settings fields — don't reject them


settings = Settings()
