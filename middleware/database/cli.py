"""
database/cli.py — command-line interface for all database operations.

Usage:
    python -m database.cli init       # create tables (first run)
    python -m database.cli migrate    # run pending Alembic migrations
    python -m database.cli seed       # populate with test data
    python -m database.cli reset      # drop + recreate + seed (dev only)
    python -m database.cli status     # show table row counts
    python -m database.cli rollback   # roll back last migration
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import argparse
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from database.models import Base, create_tables, get_engine

try:
    from config import settings
    DB_URL = settings.database_url
except Exception:
    DB_URL = os.environ.get(
        "DATABASE_URL",
        "postgresql://middleware:middleware@localhost:5432/maritime_middleware"
    )


def cmd_init(args):
    """Create all tables directly from SQLAlchemy models (no Alembic)."""
    print(f"Initialising database: {DB_URL}")
    engine = get_engine(DB_URL)
    create_tables(engine)
    print("✓  All tables created.")


def cmd_migrate(args):
    """Run pending Alembic migrations."""
    from alembic.config import Config
    from alembic import command

    alembic_cfg = Config("alembic.ini")
    print("Running migrations...")
    command.upgrade(alembic_cfg, "head")
    print("✓  Migrations applied.")


def cmd_rollback(args):
    """Roll back the last Alembic migration."""
    from alembic.config import Config
    from alembic import command

    alembic_cfg = Config("alembic.ini")
    print("Rolling back last migration...")
    command.downgrade(alembic_cfg, "-1")
    print("✓  Rolled back.")


def cmd_seed(args):
    """Populate the database with realistic test data."""
    from database.seed import seed
    seed(DB_URL, clear_existing=not args.append)


def cmd_reset(args):
    """Drop all tables and recreate with seed data. DEV ONLY."""
    confirm = input("This will DELETE all data. Type 'yes' to confirm: ")
    if confirm.strip().lower() != "yes":
        print("Aborted.")
        return

    print("Dropping all tables...")
    engine = get_engine(DB_URL)
    Base.metadata.drop_all(engine)
    print("Recreating tables...")
    create_tables(engine)
    print("Seeding...")
    from database.seed import seed
    seed(DB_URL, clear_existing=False)
    print("✓  Reset complete.")


def cmd_status(args):
    """Print row counts for all tables."""
    engine = get_engine(DB_URL)
    tables = [
        "inbound_orders",
        "cached_vessels",
        "signal_cache_refreshes",
        "match_results",
    ]
    print(f"\nDatabase: {DB_URL}\n")
    with Session(engine) as session:
        for table in tables:
            try:
                count = session.execute(
                    text(f"SELECT COUNT(*) FROM {table}")
                ).scalar()
                print(f"  {table:<30} {count:>6} rows")
            except Exception as e:
                print(f"  {table:<30} ERROR: {e}")

    # Latest cache refresh
    try:
        with Session(engine) as session:
            result = session.execute(
                text(
                    "SELECT refreshed_at, vessel_count, success, duration_ms "
                    "FROM signal_cache_refreshes "
                    "ORDER BY refreshed_at DESC LIMIT 1"
                )
            ).fetchone()
            if result:
                print(f"\n  Last Signal refresh: {result[0]}  "
                      f"({result[1]} vessels, {result[3]}ms, "
                      f"{'✓' if result[2] else '✗'})")
    except Exception:
        pass
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Maritime Middleware — database CLI"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Create tables from models (first run)")
    sub.add_parser("migrate", help="Run pending Alembic migrations")
    sub.add_parser("rollback", help="Roll back last migration")

    seed_p = sub.add_parser("seed", help="Populate with test data")
    seed_p.add_argument(
        "--append", action="store_true",
        help="Add to existing data instead of clearing first"
    )

    sub.add_parser("reset", help="Drop all data and reseed (dev only)")
    sub.add_parser("status", help="Show row counts for all tables")

    args = parser.parse_args()

    commands = {
        "init": cmd_init,
        "migrate": cmd_migrate,
        "rollback": cmd_rollback,
        "seed": cmd_seed,
        "reset": cmd_reset,
        "status": cmd_status,
    }
    commands[args.command](args)
