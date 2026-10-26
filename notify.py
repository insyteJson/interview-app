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
    # app passwords are shown in groups ("abcd efgh ..."); copied text may carry normal or non-breaking spaces
    pw = "".join(str(cfg("SMTP_PASSWORD") or "").split())
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


def diagnose(s: dict, timeout: float = 15) -> list[tuple[str, bool, str]]:
    """Walks through the connection step by step and reports where it stops: reach the server, encrypt, log in,
    send. Tries port 587 (STARTTLS) and, if that cannot connect, port 465 (SSL)."""
    import socket
    steps: list[tuple[str, bool, str]] = []
    ports = [s["port"]] + ([465] if s["port"] == 587 else [])
    for port in ports:
        try:
            with socket.create_connection((s["host"], port), timeout=timeout):
                pass
            steps.append((f"Reach {s['host']}:{port}", True, ""))
        except Exception as exc:
            steps.append((f"Reach {s['host']}:{port}", False, f"{type(exc).__name__}: {exc}"))
            continue
        try:
            if port == 465:
                smtp = smtplib.SMTP_SSL(s["host"], port, timeout=timeout, context=ssl.create_default_context())
                steps.append(("Encrypted connection (SSL)", True, ""))
            else:
                smtp = smtplib.SMTP(s["host"], port, timeout=timeout)
                smtp.ehlo()
                if smtp.has_extn("starttls"):
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                    steps.append(("Encrypted connection (STARTTLS)", True, ""))
        except Exception as exc:
            steps.append(("Encrypted connection", False, f"{type(exc).__name__}: {exc}"))
            continue
        try:
            if smtp.has_extn("auth"):
                smtp.login(s["user"], s["password"])
            steps.append((f"Log in as {s['user']}", True, ""))
        except smtplib.SMTPAuthenticationError as exc:
            steps.append((f"Log in as {s['user']}", False, "The server rejected the login: check SMTP_PASSWORD "
                          "(Gmail needs an app password) and SMTP_USER. " + str(exc.smtp_error[:120])))
            return steps
        except smtplib.SMTPServerDisconnected as exc:
            steps.append((f"Log in as {s['user']} (port {port})", False,
                          f"{type(exc).__name__}: {exc}. Gmail usually does this when it does not accept the login."))
            continue                                   # try the other port before giving up
        except Exception as exc:
            steps.append((f"Log in as {s['user']}", False, f"{type(exc).__name__}: {exc}"))
            return steps
        try:
            msg = star_message(s, "TEST", "this is a test")
            msg.replace_header("Subject", "Test: interview app notifications work")
            smtp.send_message(msg)
            smtp.quit()
            steps.append(("Send the test email", True, f"sent to {s['to']}" +
                          (f" · works on port {port}: set SMTP_PORT = \"{port}\"" if port != s["port"] else "")))
        except Exception as exc:
            steps.append(("Send the test email", False, f"{type(exc).__name__}: {exc}"))
        return steps
    return steps


def recruiter_finished_message(s: dict, rec: dict, reviews: list[dict], csv_text: str) -> EmailMessage:
    """Summary of one recruiter's ratings when they reach the thank-you page, with all answers as CSV."""
    import statistics as stx

    def mean(a, keys):
        vals = [a.get(k) for k in keys if isinstance(a.get(k), (int, float))]
        return f"{stx.mean(vals):.1f}" if vals else "–"

    done = [r for r in reviews if r["status"] == "complete"]
    skipped = sum(r["status"] == "skipped" for r in reviews)
    lines = [f"{rec['code']} reached the thank-you page.", "",
             f"Recruiter: {rec['code']} · invited by {rec.get('invited_by') or '–'} · experience "
             f"{rec.get('experience') or '–'} · domain {rec.get('domain') or '–'}"]
    if rec.get("comment"):
        lines.append(f"Comment: {rec['comment']}")
    lines += [f"Candidates rated: {len(done)}" + (f" (skipped as recognised: {skipped})" if skipped else ""), "",
              "Per candidate, resume / CV (1-7; usefulness and coverage are means of 4 items each):"]
    for r in done:
        a = r["answers"] or {}
        secs = sum((v or {}).get("seconds") or 0 for v in (r["screens"] or {}).values())
        pref = {"profile": "resume", "cv": "CV", "equal": "equal"}.get(a.get("pref_doc"), a.get("pref_doc") or "–")
        lines.append(
            f"- {r['session_id']} ({'resume' if r['doc_order'] == 'profile-first' else 'CV'} first): usefulness "
            f"{mean(a, [f'profile_u{i}' for i in range(1, 5)])} / {mean(a, [f'cv_u{i}' for i in range(1, 5)])} · "
            f"coverage {mean(a, [f'profile_c{i}' for i in range(1, 5)])} / "
            f"{mean(a, [f'cv_c{i}' for i in range(1, 5)])} · rely {a.get('profile_r1', '–')} / {a.get('cv_r1', '–')}"
            f" · distinguish {a.get('profile_r2', '–')} / {a.get('cv_r2', '–')} · prefers {pref} · "
            f"{secs / 60:.1f} min")
        if a.get("missing"):
            lines.append(f"    missing in both: {a['missing']}")
    lines += ["", "All answers are attached as CSV (same columns as the export in the researcher view)."]
    if s.get("admin_url"):
        lines.append(f"Researcher view: {s['admin_url']}")
    msg = EmailMessage()
    msg["Subject"] = f"{rec['code']} finished: {len(done)} candidate{'s' if len(done) != 1 else ''} rated"
    msg["From"] = s["user"]
    msg["To"] = s["to"]
    msg.set_content("\n".join(lines) + "\n\n— Interview app")
    msg.add_attachment(csv_text.encode("utf-8"), maintype="text", subtype="csv",
                       filename=f"{rec['code']} - ratings.csv")
    return msg
