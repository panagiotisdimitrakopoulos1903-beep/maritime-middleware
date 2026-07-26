"""
main.py — entry point.

Run with:
    uvicorn main:app --host 127.0.0.1 --port 5000 --reload

The Milter process runs separately:
    python -m milter.hook
"""
from api.app import app  # noqa: F401 — imported for uvicorn

if __name__ == "__main__":
    import uvicorn
    from config import settings
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level="info",
    )
