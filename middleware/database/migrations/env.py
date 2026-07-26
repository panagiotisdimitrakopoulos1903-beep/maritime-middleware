"""
database/migrations/env.py — Alembic migration environment.

Reads the database URL from .env (same source as the app),
and imports the SQLAlchemy models so Alembic can auto-detect schema changes.
"""
import os
import sys
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

# Make sure the project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# Import models so Alembic sees them for autogenerate
from database.models import Base  # noqa: F401

# Load settings for database URL
try:
    from config import settings
    db_url = settings.database_url
except Exception:
    # Fallback if .env is not present during CI
    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://middleware:middleware@localhost:5432/maritime_middleware"
    )

config = context.config
config.set_main_option("sqlalchemy.url", db_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    Run migrations without a live database connection.
    Outputs SQL to stdout — useful for review before applying.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Run migrations against a live database connection.
    This is the normal mode used in deployment.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
