import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import notify  # noqa: E402

aiosmtpd = pytest.importorskip("aiosmtpd")
from aiosmtpd.controller import Controller  # noqa: E402


class Inbox:
    def __init__(self):
        self.messages = []

    async def handle_DATA(self, server, session, envelope):
        self.messages.append(envelope.content.decode("utf-8", "replace"))
        return "250 OK"


@pytest.fixture
def smtp():
    inbox = Inbox()
    ctl = Controller(inbox, hostname="127.0.0.1", port=8025)
    ctl.start()
    yield inbox
    ctl.stop()


def cfg_from(d):
    return lambda k, default=None: d.get(k, default)


CFG = {"NOTIFY_EMAIL_TO": "me@example.com", "SMTP_PASSWORD": "x", "SMTP_HOST": "127.0.0.1", "SMTP_PORT": "8025",
       "APP_URL": "https://app.example/"}


def test_not_configured_sends_nothing():
    assert notify.settings(cfg_from({})) is None
    assert notify.settings(cfg_from({"NOTIFY_EMAIL_TO": "me@example.com"})) is None
    assert notify.send_in_background(None, None) is None


def test_star_email_arrives_with_code_counts_and_link_only(smtp):
    s = notify.settings(cfg_from(CFG))
    t = notify.send_in_background(s, notify.star_message(s, "P12", "6 situations from 3 roles"))
    t.join(10)
    assert len(smtp.messages) == 1
    import email
    from email import policy
    m = email.message_from_string(smtp.messages[0], policy=policy.default)
    body = m.get_content()
    assert m["Subject"] == "★ P12 starred for the recruiter study" and m["To"] == "me@example.com"
    assert "6 situations from 3 roles" in body and "https://app.example/?admin=1" in body


def test_a_broken_mail_server_never_raises():
    s = notify.settings(cfg_from({**CFG, "SMTP_PORT": "8999"}))     # nothing listens there
    t0 = time.time()
    t = notify.send_in_background(s, notify.star_message(s, "P12", "x"))
    assert time.time() - t0 < 0.5                                     # the caller does not wait
    t.join(20)                                                        # the thread ends quietly


def test_cv_hook_sends_only_when_starred(smtp, tmp_path, monkeypatch):
    import autostar
    from storage import Store
    store = Store(f"sqlite:///{tmp_path}/n.db")
    base = {"rule": "r", "phase_before": "start", "meta": None, "accepted": 0, "declined": 0, "audit": "",
            "audit_note": "", "turn": 0, "answer_raw": "", "question_shown": "q", "qid": "", "classification": None}
    for sid, n in (("P20", 5), ("P21", 2)):
        incs = [{"id": f"i{k}", "position_label": f"R{k % 2}", "dimension": "ability"} for k in range(n)]
        store.create_session(sid, {"status": "complete", "incidents": incs, "positions": []}, base)
        store.save_cv(sid, b"%PDF", {})
        # the code the CV step runs after saving
        if autostar.evaluate(store, sid):
            s = notify.settings(cfg_from(CFG))
            reason = store.list_auto_decisions()[sid]["reason"]
            notify.send_in_background(s, notify.star_message(s, sid, reason)).join(10)
    assert len(smtp.messages) == 1 and "P20" in smtp.messages[0]
