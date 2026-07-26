"""
milter/hook.py — Postfix Milter integration.

Intercepts inbound emails mid-delivery (before they reach WT3),
extracts the message body, and passes it to the processing pipeline
via an internal HTTP call to the FastAPI backend.

Run this as a separate process:
    python -m milter.hook

Postfix config (main.cf):
    smtpd_milters = inet:localhost:9900
    milter_default_action = accept
"""
import email
import httpx
import structlog
import Milter
from Milter.utils import parse_addr

from config import settings

log = structlog.get_logger()

BACKEND_INGEST_URL = f"http://{settings.host}:{settings.port}/internal/ingest"


class MaritimeMilter(Milter.Base):
    """
    Milter that fires on every inbound email.
    Passes a copy to the middleware backend without affecting delivery.
    """

    def __init__(self):
        self._body_chunks: list[bytes] = []
        self._sender: str = ""
        self._recipients: list[str] = []
        self._headers: dict = {}

    @Milter.noreply
    def connect(self, IPname, family, hostaddr):
        return Milter.CONTINUE

    @Milter.noreply
    def envfrom(self, mailfrom, *str):
        self._sender = mailfrom
        self._body_chunks = []
        self._headers = {}
        self._recipients = []
        return Milter.CONTINUE

    @Milter.noreply
    def envrcpt(self, to, *str):
        self._recipients.append(to)
        return Milter.CONTINUE

    @Milter.noreply
    def header(self, name, hval):
        self._headers[name] = hval
        return Milter.CONTINUE

    @Milter.noreply
    def body(self, chunk):
        self._body_chunks.append(chunk)
        return Milter.CONTINUE

    def eom(self):
        """
        End of message — full email is now available.
        Fire-and-forget POST to the backend ingest endpoint.
        Does NOT block delivery (returns CONTINUE regardless).
        """
        try:
            raw_body = b"".join(self._body_chunks).decode("utf-8", errors="replace")

            # Parse MIME — extract plain text body
            msg = email.message_from_string(raw_body)
            text_body = _extract_plain_text(msg)

            if not text_body or len(text_body.strip()) < 10:
                log.debug("milter.skipped_empty_body")
                return Milter.CONTINUE

            payload = {
                "sender": self._sender,
                "subject": self._headers.get("Subject", ""),
                "raw_body": text_body,
            }

            # Non-blocking HTTP POST to local FastAPI backend
            with httpx.Client(timeout=5.0) as client:
                response = client.post(BACKEND_INGEST_URL, json=payload)
                if response.status_code != 202:
                    log.warning(
                        "milter.ingest_unexpected_status",
                        status=response.status_code
                    )

        except Exception as e:
            log.error("milter.eom_error", error=str(e))
            # Always continue — never block email delivery due to middleware error

        return Milter.CONTINUE

    def abort(self):
        return Milter.CONTINUE

    def close(self):
        return Milter.CONTINUE


def _extract_plain_text(msg: email.message.Message) -> str:
    """Extract plain text from a MIME message, handling multipart."""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain":
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8",
                        errors="replace"
                    )
                except Exception:
                    continue
        return ""
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                return payload.decode(
                    msg.get_content_charset() or "utf-8",
                    errors="replace"
                )
        except Exception:
            pass
        return str(msg.get_payload())


def run_milter():
    """Start the Milter listener. Blocks until interrupted."""
    log.info("milter.starting", socket=settings.milter_socket)
    Milter.factory = MaritimeMilter
    Milter.set_flags(Milter.CHGBODY + Milter.CHGHDRS + Milter.ADDHDRS)
    Milter.runmilter(settings.milter_name, settings.milter_socket, timeout=300)


if __name__ == "__main__":
    run_milter()
