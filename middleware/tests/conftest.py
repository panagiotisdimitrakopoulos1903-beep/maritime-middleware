"""
tests/conftest.py — shared pytest fixtures / test environment setup.

config.py's Settings() requires ANTHROPIC_API_KEY and SIGNAL_OCEAN_API_KEY
with no default (see config.py), so importing almost anything in this
codebase (matching.engine, scheduler.jobs, api.app, ...) fails at collection
time unless those are present in the environment. Real local dev supplies
them via middleware/.env (see CLAUDE.md "Running it locally"), but that file
is git-ignored and not guaranteed to exist wherever tests run (e.g. CI, a
fresh checkout). Fall back to harmless dummy values so the test suite is
collectible on its own — `setdefault` never overrides real keys if they're
already exported.
"""
import os

os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy-key-not-real")
os.environ.setdefault("SIGNAL_OCEAN_API_KEY", "test-dummy-key-not-real")
