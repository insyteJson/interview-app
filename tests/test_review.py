import os
import random
import sys
from datetime import datetime, timedelta, timezone

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_DIR)

import pymupdf  # noqa: E402
import pytest  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

import review as rv  # noqa: E402
from storage import InviteError, Store  # noqa: E402


def pdf(text):
    d = pymupdf.open()
    d.new_page().insert_text((72, 72), text)
    b = d.tobytes()
    d.close()
    return b


def seed(store, code, fav=True, checked=True, approved=True):
    store.create_session(code, {"status": "complete", "turn": 5, "positions": [], "incidents": [], "phase": "done",
                                "pos_idx": 0},
                         {"turn": 0, "answer_raw": "", "question_shown": "q", "rule": "", "qid": "",
                          "phase_before": "start", "classification": None, "meta": None, "accepted": 0,
                          "declined": 0, "audit": "", "audit_note": ""})
    store.save_cv(code, pdf(f"CV {code}"), {})
    store.mark_cv_checked(code, checked)
    pid = store.add_profile(code, "resume-v3", True, "t", {}, {}, pdf(f"Resume {code}"))
    store.approve_profile(pid, approved)
    store.set_favorite(code, fav)
    return pid


def consent():
    return {"version": "1.0", "statements": [1, 2, 3, 4], "at": "now"}


@pytest.fixture
def store(tmp_path):
    return Store(f"sqlite:///{tmp_path}/r.db")


# ------------------------------------------------------------------ queue

def test_pool_is_ready_favourites_only(store):
    seed(store, "P01")
    seed(store, "P02", fav=False)
    seed(store, "P03", checked=False)
    seed(store, "P04", approved=False)
    assert [p["session_id"] for p in rv.ready_pool(store)] == ["P01"]


def test_queue_balances_candidates_and_document_order(store):
    for c in ("P01", "P02", "P03"):
        seed(store, c)
    rng = random.Random(1)
    counts = {}
    orders = {}
    for i in range(6):
        code = f"R{i + 1:02d}"
        for _ in range(3):
            nxt = rv.pick_next(store, code, rng=rng)
            r = store.create_review(code, **nxt)
            store.save_review_screen(r["id"], "compare", {"pref": "Equally / Gleich"}, 1, complete=True)
            counts[nxt["session_id"]] = counts.get(nxt["session_id"], 0) + 1
            orders.setdefault(nxt["session_id"], []).append(nxt["doc_order"])
        assert rv.pick_next(store, code, rng=rng) is None          # never the same candidate twice
    assert set(counts.values()) == {6}                              # every candidate rated equally often
    assert all(o.count("profile-first") == 3 for o in orders.values())   # order balanced per candidate


def test_no_candidate_is_shown_to_more_than_ten_recruiters(store):
    seed(store, "P01")
    for i in range(10):
        r = store.create_review(f"R{i + 1:02d}", "P01", 1, "cv-first")
        store.skip_review(r["id"]) if i % 2 else None              # skipped ones count as shown
    assert rv.pick_next(store, "R11") is None


def test_stale_reservations_are_released_for_balancing(store):
    seed(store, "P01")
    seed(store, "P02")
    store.create_review("R01", "P01", 1, "cv-first")                # in progress, fresh
    assert rv.pick_next(store, "R02")["session_id"] == "P02"
    later = datetime.now(timezone.utc) + timedelta(hours=rv.RESERVE_HOURS + 1)
    picks = {rv.pick_next(store, "R02", rng=random.Random(s), now=later)["session_id"] for s in range(20)}
    assert picks == {"P01", "P02"}                                  # reservation expired: both equally low


# ------------------------------------------------------------------ invites

def test_invites_are_single_use_and_can_expire_or_be_revoked(store):
    t1 = store.create_invite("researcher")
    rec = store.redeem_invite(t1, consent())
    assert rec["code"] == "R01" and rec["invited_by"] == "researcher" and len(rec["key"]) >= 8
    with pytest.raises(InviteError):
        store.redeem_invite(t1, consent())
    t2 = store.create_invite("R01", days=-1)
    assert store.invite_problem(store.get_invite(t2)) == "expired"
    t3 = store.create_invite("R01")
    store.revoke_invite(t3)
    assert store.invite_problem(store.get_invite(t3)) == "revoked"
    assert store.redeem_invite(store.create_invite("R01"), consent())["code"] == "R02"


def test_signups_carry_no_recruiter_code_or_time(store):
    store.add_signup(" a@b.de ", rv.SIGNUP_VERSION)
    row = store.list_signups()[0]
    assert set(row) == {"id", "email", "consent_version", "signup_date"} and row["email"] == "a@b.de"
    assert len(row["signup_date"]) == 10


# ------------------------------------------------------------------ page

def app(tmp_path, monkeypatch, param):
    import streamlit as st
    st.cache_resource.clear()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/r.db")
    monkeypatch.setenv("APP_URL", "https://example.app/")
    monkeypatch.chdir(APP_DIR)
    at = AppTest.from_file(os.path.join(APP_DIR, "app.py"), default_timeout=60)
    at.query_params["review"] = param
    at.session_state["_classifier_override"] = object()
    return at


def rate_document(at, value=5):
    for sc in [w for w in at.segmented_control if w.key and w.key.split("_")[-2] in ("profile", "cv")]:
        pass
    keys = [w.key for w in at.segmented_control if w.key and w.key.split("_")[-2] in ("profile", "cv")]
    assert len(keys) == 10, keys
    for k in keys:
        at.segmented_control(key=k).set_value(value).run()
        assert not at.exception, at.exception


def text(at) -> str:
    return " ".join(m.value for m in at.markdown)


def test_full_recruiter_journey(tmp_path, monkeypatch, store):
    for c in ("P01", "P02", "P03", "P04"):
        seed(store, c)
    token = store.create_invite("researcher")
    at = app(tmp_path, monkeypatch, token)
    at.run()
    assert not at.exception, at.exception
    for i in range(1, 5):
        at.checkbox(key=f"rv_c{i}").check()
    at.button[0].click().run()
    assert not at.exception, at.exception
    code_param = at.query_params["review"]
    code_param = code_param[0] if isinstance(code_param, list) else code_param
    assert code_param.startswith("R01.")
    assert "one-minute guide" in text(at)
    import guide
    for _ in range(len(guide.STEPS) - 1):
        at.button(key="rv_R01_tour_next").click().run()
    at.button(key="rv_R01_tour_done").click().run()
    assert not at.exception, at.exception
    assert store.get_recruiter("R01")["guide_done"] == 1

    for n in range(1, 4):
        assert f"Candidate {n}" in text(at), text(at)[:300]
        rate_document(at)                                       # Document A: 10 clicks, moves on by itself
        assert "step 2 of 3" in text(at)
        rate_document(at, value=6)                              # Document B
        assert "step 3 of 3" in text(at)
        rid = [r for r in store.list_reviews(recruiter="R01") if r["status"] == "in_progress"][0]["id"]
        at.segmented_control(key=f"rv_R01_{rid}_pref").set_value("Document B").run()
        at.button(key=f"rv_R01_{rid}_finish").click().run()
        assert not at.exception, at.exception

    assert "That's the 3 you signed up for" in " ".join(s.value for s in at.success)
    assert at.button(key="rv_R01_another").disabled                # experience is required first
    at.segmented_control(key="rv_R01_exp").set_value("2–5").run()
    at.text_input(key="rv_R01_dom").input("IT").run()
    at.button(key="rv_R01_stop").click().run()
    assert not at.exception, at.exception
    rec = store.get_recruiter("R01")
    assert rec["experience"] == "2–5" and rec["domain"] == "IT"

    # thank-you screen: signup and share
    at.button(key="rv_R01_signup_btn").click().run()
    at.text_input(key="rv_R01_signup_email").input("recruiter@example.com")
    at.checkbox(key="rv_R01_signup_agree").check()
    at.button[[b.label for b in at.button].index("Sign up · Eintragen")].click().run()
    assert store.list_signups()[0]["email"] == "recruiter@example.com"
    at.button(key="rv_R01_share").click().run()
    assert any(i["created_by"] == "R01" for i in store.list_invites())

    done = [r for r in store.list_reviews(recruiter="R01") if r["status"] == "complete"]
    assert len(done) == 3
    a = done[0]["answers"]
    assert len([k for k in a if k.startswith(("profile_", "cv_"))]) == 20 and a["pref"] == "Document B"
    first = rv.screens(done[0]["doc_order"])[1][1]
    assert a["pref_doc"] == first and a[f"{first}_u1"] == 6       # B was rated 6 and preferred


def test_recognised_candidate_is_skipped_and_replaced(tmp_path, monkeypatch, store):
    seed(store, "P01")
    seed(store, "P02")
    rec = store.redeem_invite(store.create_invite("researcher"), consent())
    store.update_recruiter("R01", guide_done=1)
    at = app(tmp_path, monkeypatch, f"R01.{rec['key']}")
    at.run()
    assert not at.exception, at.exception
    first = store.list_reviews(recruiter="R01")[0]
    at.button(key=f"rv_R01_{first['id']}_skip").click().run()
    assert not at.exception, at.exception
    rs = store.list_reviews(recruiter="R01")
    assert [r["status"] for r in rs] == ["skipped", "in_progress"] and rs[0]["session_id"] != rs[1]["session_id"]


def test_bad_links_are_refused(tmp_path, monkeypatch, store):
    rec = store.redeem_invite(store.create_invite("researcher"), consent())
    at = app(tmp_path, monkeypatch, "R01.wrongkey123")
    at.run()
    assert "not valid" in at.error[0].value
    at = app(tmp_path, monkeypatch, "inv-doesnotexist")
    at.run()
    assert "not valid" in at.error[0].value
    assert rec["code"] == "R01"


def test_reload_after_third_candidate_still_asks_experience(tmp_path, monkeypatch, store):
    for c in ("P01", "P02", "P03", "P04"):
        seed(store, c)
    rec = store.redeem_invite(store.create_invite("researcher"), consent())
    store.update_recruiter("R01", guide_done=1)
    for c in ("P01", "P02", "P03"):
        r = store.create_review("R01", c, 1, "cv-first")
        store.save_review_screen(r["id"], "compare", {"pref": "Document A"}, 1, complete=True)
    at = app(tmp_path, monkeypatch, f"R01.{rec['key']}")
    at.run()
    assert not at.exception, at.exception
    assert at.segmented_control(key="rv_R01_exp") is not None
    assert [r for r in store.list_reviews(recruiter="R01") if r["status"] == "in_progress"] == []


def test_export_has_one_row_per_recruiter_and_candidate_with_flag(store):
    import admin
    seed(store, "P01")
    r = store.create_review("R01", "P01", 1, "profile-first")
    same = {k: 4 for k in rv.doc_keys("profile") + rv.doc_keys("cv")}
    store.save_review_screen(r["id"], "compare", {**same, "pref": "Equally / Gleich", "pref_doc": "equal"}, 2,
                             complete=True)
    t = admin.reviews_table(store)
    assert list(t["recruiter"]) == ["R01"] and t.loc[0, "profile_u1"] == 4 and bool(t.loc[0, "flag_identical"])
    assert {"cv_r2", "sec_A", "sec_compare", "pref_doc", "experience"} <= set(t.columns)
