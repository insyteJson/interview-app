"""Email to the researcher when a session is starred automatically.

Configured in the secrets (all optional; without NOTIFY_EMAIL_TO and SMTP_PASSWORD nothing is sent):
    NOTIFY_EMAIL_TO = "you@example.com"
    SMTP_USER       = "you@gmail.com"          # the account that sends (defaults to NOTIFY_EMAIL_TO)
    SMTP_PASSWORD   = "app password"           # Gmail: an app password, not the normal password
    SMTP_HOST       = "smtp.gmail.com"         # default
    SMTP_PORT       = "587"                    # default (STARTTLS); 465 uses SSL

The email contains only the participant code and counts. Sending runs in a background thread with a timeout, so a
participant never waits for it and a failure never reaches them.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import threading
from email.message import EmailMessage

log = logging.getLogger("interview")


def settings(cfg) -> dict | None:
    to = (cfg("NOTIFY_EMAIL_TO") or "").strip()
    pw = (cfg("SMTP_PASSWORD") or "").strip()
    if not to or not pw:
        return None
    return {"to": to, "user": (cfg("SMTP_USER") or to).strip(), "password": pw,
            "host": (cfg("SMTP_HOST") or "smtp.gmail.com").strip(), "port": int(cfg("SMTP_PORT") or 587),
            "admin_url": (cfg("APP_URL") or "").rstrip("/") + "/?admin=1" if cfg("APP_URL") else ""}


def star_message(s: dict, code: str, reason: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = f"★ {code} starred for the recruiter study"
    msg["From"] = s["user"]
    msg["To"] = s["to"]
    link = f"\n\nResearcher view: {s['admin_url']}" if s["admin_url"] else ""
    msg.set_content(
        f"{code} has just submitted their CV and was starred automatically ({reason}).\n\n"
        "Next steps before recruiters can see this candidate:\n"
        "  1. Profile: remove names, check merges, generate, check and approve (Step 1-4)\n"
        "  2. CV: look through the blacked-out CV and tick 'I checked this CV by hand'"
        f"{link}\n\n— Interview app")
    return msg


def send(s: dict, msg: EmailMessage, timeout: float = 15) -> None:
    if s["port"] == 465:
        with smtplib.SMTP_SSL(s["host"], s["port"], timeout=timeout, context=ssl.create_default_context()) as smtp:
            smtp.login(s["user"], s["password"])
            smtp.send_message(msg)
        return
    with smtplib.SMTP(s["host"], s["port"], timeout=timeout) as smtp:
        smtp.ehlo()
        if smtp.has_extn("starttls"):
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        if smtp.has_extn("auth"):
            smtp.login(s["user"], s["password"])
        smtp.send_message(msg)


def send_in_background(s: dict | None, msg: EmailMessage) -> threading.Thread | None:
    if not s:
        return None

    def run():
        try:
            send(s, msg)
        except Exception:
            log.exception("Notification email could not be sent")

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t
