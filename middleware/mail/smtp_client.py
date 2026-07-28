"""
mail/smtp_client.py — outbound SMTP send.

Symmetric with mcp/imap_client.py's design: stdlib protocol library
(smtplib + email.message.EmailMessage), credentials from config.py's
Settings object. Used directly by POST /internal/send (api/app.py, ADR
0004, Decision #5) — NOT an MCP tool. This mirrors how signal_client/
client.py is a plain module consumed directly by api/app.py, distinct from
its MCP-exposed twin mcp/signal_server.py; SMTP send has no MCP-exposed
twin at all in this change.

Raises on failure rather than logging-and-swallowing — the whole point of
ADR 0004 Decision #5 is that send failures must surface to the caller (the
broker is watching a Send button and needs a definite result). This module
deliberately does not catch smtplib/socket exceptions itself; it lets them
propagate for api/app.py's endpoint handler to translate into a clean HTTP
error.
"""
import smtplib
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Optional

from config import settings


class SMTPNotConfiguredError(RuntimeError):
    """Raised when SMTP_HOST/SMTP_USER/SMTP_PASS are not set in .env."""
    pass


def _require_configured() -> None:
    if not (settings.smtp_host and settings.smtp_user and settings.smtp_pass):
        raise SMTPNotConfiguredError(
            "SMTP is not configured — set SMTP_HOST, SMTP_USER, SMTP_PASS in .env"
        )


def send_email(
    sender: str,
    to: str,
    subject: str,
    body: str,
    cc: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
) -> str:
    """
    Send a plain-text email via the broker's real SMTP mailbox.

    Builds an RFC 2822 EmailMessage with From/To/Cc/Subject/plain-text body,
    a freshly generated Message-ID (always, regardless of whether this is a
    reply), and In-Reply-To/References headers when `in_reply_to` is given —
    the caller (api/app.py's /internal/send handler) derives those values
    from the parent InboundOrder row per standard RFC 2822 reply semantics
    before calling this function; this module just sets whatever headers
    it's handed.

    Uses smtplib.SMTP + STARTTLS on the configured port (587 is the
    standard STARTTLS submission port and the primary path this supports;
    implicit-SSL-on-465 is not implemented since it isn't needed for the
    target mailbox setup). Set SMTP_USE_TLS=false to skip STARTTLS entirely
    (e.g. a local test SMTP server with no TLS support).

    Returns the generated Message-ID string on success. Raises
    SMTPNotConfiguredError if SMTP_HOST/USER/PASS aren't set, or whatever
    smtplib/socket exception occurs on failure (auth failure, connection
    refused, recipients refused, timeout, DNS failure, etc.) — callers must
    catch and translate; this function does not swallow errors.
    """
    _require_configured()

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    message_id = make_msgid()
    msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.set_content(body)

    recipients = [to]
    if cc:
        recipients += [addr.strip() for addr in cc.split(",") if addr.strip()]

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
        if settings.smtp_use_tls:
            server.starttls()
        server.login(settings.smtp_user, settings.smtp_pass)
        server.send_message(msg, from_addr=sender, to_addrs=recipients)

    return message_id
