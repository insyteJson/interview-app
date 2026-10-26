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


def test_diagnose_reports_each_step(smtp):
    s = notify.settings(cfg_from(CFG))
    steps = notify.diagnose(s)
    assert [ok for _, ok, _ in steps] == [True, True, True] and steps[-1][0] == "Send the test email"
    assert len(smtp.messages) == 1


def test_diagnose_shows_where_it_stops():
    s = notify.settings(cfg_from({**CFG, "SMTP_PORT": "8999"}))
    steps = notify.diagnose(s, timeout=3)
    assert steps[0][0].startswith("Reach") and steps[0][1] is False


def test_password_spaces_are_removed():
    s = notify.settings(cfg_from({**CFG, "SMTP_PASSWORD": "abcd efgh\u00a0ijkl mnop "}))
    assert s["password"] == "abcdefghijklmnop"


def test_recruiter_finish_email_arrives_once_with_summary_and_csv(smtp, tmp_path, monkeypatch):
    import email
    from email import policy
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
    import test_review as tr
    from storage import Store
    store = Store(f"sqlite:///{tmp_path}/r.db")
    for c in ("P01", "P02", "P03"):
        tr.seed(store, c)
    rec = store.redeem_invite(store.create_invite("researcher"), tr.consent())
    store.update_recruiter("R01", guide_done=1)
    for k, v in {"NOTIFY_EMAIL_TO": "me@example.com", "SMTP_PASSWORD": "x", "SMTP_HOST": "127.0.0.1",
                 "SMTP_PORT": "8025"}.items():
        monkeypatch.setenv(k, v)
    at = tr.app(tmp_path, monkeypatch, f"R01.{rec['key']}")
    at.run()
    for _ in range(3):
        tr.rate_document(at, 6)
        tr.rate_document(at, 3)
        rid = [r for r in store.list_reviews(recruiter="R01") if r["status"] == "in_progress"][0]["id"]
        at.segmented_control(key=f"rv_R01_{rid}_pref").set_value("Document A").run()
        at.text_area(key=f"rv_R01_{rid}_missing").input("salary expectations").run()
        at.button(key=f"rv_R01_{rid}_finish").click().run()
    assert not smtp.messages                                    # not before the thank-you page
    at.segmented_control(key="rv_R01_exp").set_value("6–10").run()
    at.text_input(key="rv_R01_dom").input("Finance").run()
    at.button(key="rv_R01_stop").click().run()
    assert not at.exception, at.exception
    for _ in range(50):
        if smtp.messages:
            break
        time.sleep(0.1)
    at.run()                                                    # the page re-renders: no second email
    time.sleep(0.5)
    assert len(smtp.messages) == 1
    m = email.message_from_string(smtp.messages[0], policy=policy.default)
    assert m["Subject"] == "R01 finished: 3 candidates rated"
    body = m.get_body(preferencelist=("plain",)).get_content()
    assert "experience 6–10 · domain Finance" in body and "Candidates rated: 3" in body
    assert "usefulness 6.0 / 3.0" in body or "usefulness 3.0 / 6.0" in body
    assert "missing in both: salary expectations" in body
    att = [p for p in m.iter_attachments()]
    assert att and att[0].get_filename() == "R01 - ratings.csv"
    csv = att[0].get_content()
    csv = csv.decode() if isinstance(csv, bytes) else csv
    assert csv.count("\n") == 4 and "profile_u1" in csv          # header + 3 candidates
