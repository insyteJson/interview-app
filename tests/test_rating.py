import os
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_DIR)

import pymupdf  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

import rating as rt  # noqa: E402
from storage import Store  # noqa: E402


def pdf(text: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    out = doc.tobytes()
    doc.close()
    return out


def seed(store: Store, code: str, approved: bool = True) -> int:
    state = {"status": "complete", "turn": 5, "positions": [], "incidents": [], "phase": "done", "pos_idx": 0}
    store.create_session(code, state, {"turn": 0, "answer_raw": "", "question_shown": "q", "rule": "", "qid": "",
                                       "phase_before": "start", "classification": None, "meta": None,
                                       "accepted": 0, "declined": 0, "audit": "", "audit_note": ""})
    store.save_cv(code, pdf("CV"), {})
    pid = store.add_profile(code, "resume-v3", True, "test", {}, {}, pdf("Resume"))
    if approved:
        store.approve_profile(pid, True)
    return pid


# ------------------------------------------------------------------ pure logic

def test_order_is_counterbalanced_by_code():
    assert rt.doc_order("P01") == "profile-first"
    assert rt.doc_order("P02") == "cv-first"
    assert rt.doc_order("P10") == "cv-first" and rt.doc_order("P09") == "profile-first"


def test_profile_only_screen_always_follows_the_profile_screens():
    for order in ("profile-first", "cv-first"):
        sc = rt.screens(order)
        i = sc.index(("profile", "profile_only"))
        assert sc[i - 2:i] == [("profile", "primary"), ("profile", "diagnostic")]
        assert sc[-1] == ("final", "final") and len(sc) == 6
    assert rt.screens("cv-first")[0] == ("cv", "primary")


def test_answer_keys_are_unique_and_per_document_for_shared_items():
    keys = [rt.answer_key(s, it[0]) for s in rt.screens("profile-first") for it in rt.items(s)]
    assert len(keys) == len(set(keys))
    assert "profile_a1" in keys and "cv_a1" in keys and "cv_d8" in keys and "p9" in keys and "f12" in keys


def test_open_questions_are_never_required():
    for s in rt.screens("profile-first"):
        assert all(not k.startswith(("p11", "f13")) for k in rt.required_keys(s))
    assert rt.clicks_total("profile-first") == rt.clicks_total("cv-first") == 23


# ------------------------------------------------------------------ storage

def test_answers_merge_per_screen_and_lock_on_completion(tmp_path):
    store = Store(f"sqlite:///{tmp_path}/r.db")
    pid = seed(store, "P01")
    store.start_rating("P01", rt.INSTRUMENT, "profile-first", pid)
    store.save_rating_screen("P01", "profile.primary", {"profile_a1": 5}, 10)
    store.save_rating_screen("P01", "profile.primary", {"profile_a2": 6}, 4)
    r = store.get_rating("P01")
    assert r["answers"] == {"profile_a1": 5, "profile_a2": 6} and r["screens"]["profile.primary"]["seconds"] == 14
    store.save_rating_screen("P01", "final.final", {"f12": "Equally"}, 3, complete=True)
    store.save_rating_screen("P01", "final.final", {"f12": "Your CV"}, 3)
    assert store.get_rating("P01")["answers"]["f12"] == "Equally"          # locked after submit
    assert store.start_rating("P01", rt.INSTRUMENT, "cv-first", pid)["doc_order"] == "profile-first"


# ------------------------------------------------------------------ page

def new_app(tmp_path, monkeypatch, code):
    import streamlit as st
    st.cache_resource.clear()            # app.py caches the Store; each test uses its own database
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/r.db")
    monkeypatch.setenv("ALLOWED_CODES", "P01,P02,P03")
    monkeypatch.chdir(APP_DIR)
    at = AppTest.from_file(os.path.join(APP_DIR, "app.py"), default_timeout=30)
    at.query_params["rate"] = code
    at.session_state["_classifier_override"] = object()
    return at


def step(at) -> str:
    return next(m.value for m in at.markdown if m.value.startswith("**Step "))


def answer_screen(at, code, screen, value=5):
    for it in rt.items(screen):
        if it[1] in ("scale", "scale_na"):
            at.segmented_control(key=f"r_{code}_{rt.answer_key(screen, it[0])}").set_value(value).run()
        elif it[1] == "choice":
            at.segmented_control(key=f"r_{code}_{rt.answer_key(screen, it[0])}").set_value(it[3][-1]).run()
        assert not at.exception, at.exception


def test_not_ready_and_invalid_links(tmp_path, monkeypatch):
    store = Store(f"sqlite:///{tmp_path}/r.db")
    seed(store, "P03", approved=False)
    at = new_app(tmp_path, monkeypatch, "P03")
    at.run()
    assert not at.exception and "not ready yet" in at.info[0].value
    bad = new_app(tmp_path, monkeypatch, "X99")
    bad.run()
    assert "not valid" in bad.error[0].value


def test_full_flow_cv_first_with_back_and_resume(tmp_path, monkeypatch):
    store = Store(f"sqlite:///{tmp_path}/r.db")
    seed(store, "P02")
    sc = rt.screens("cv-first")
    at = new_app(tmp_path, monkeypatch, "P02")
    at.run()
    assert not at.exception, at.exception
    assert "Step 1 of 6" in step(at) and "Your CV" in step(at)

    answer_screen(at, "P02", sc[0])                       # 4 clicks -> moves on by itself
    assert "Step 2 of 6" in step(at)
    assert store.get_rating("P02")["answers"]["cv_a4"] == 5

    at.button(key="r_P02_back_1").click().run()           # Back keeps the saved answers
    assert "Step 1 of 6" in step(at)
    assert at.segmented_control(key="r_P02_cv_a1").value == 5
    at.button(key="r_P02_fwd_0").click().run()
    assert "Step 2 of 6" in step(at)

    answer_screen(at, "P02", sc[1], value=rt.NA)
    answer_screen(at, "P02", sc[2])
    answer_screen(at, "P02", sc[3])
    assert "Step 5 of 6" in step(at)
    answer_screen(at, "P02", sc[4])                       # text screen: does not move on by itself
    assert "Step 5 of 6" in step(at)
    assert at.button(key="r_P02_next_4").label == "Skip and continue"
    at.text_area(key="r_P02_p11").input("Entry 3 is wrong").run()
    assert at.button(key="r_P02_next_4").label == "Continue"
    at.button(key="r_P02_next_4").click().run()
    assert "Step 6 of 6" in step(at)

    # reload in a new browser: resumes at the first unsaved step
    again = new_app(tmp_path, monkeypatch, "P02")
    again.run()
    assert "Step 6 of 6" in step(again)

    assert again.button(key="r_P02_next_5").disabled
    answer_screen(again, "P02", sc[5])
    again.button(key="r_P02_next_5").click().run()
    assert not again.exception
    assert again.success and "you are done" in again.success[0].value
    assert again.get("download_button")

    r = store.get_rating("P02")
    assert r["status"] == "complete" and r["doc_order"] == "cv-first"
    assert r["answers"]["cv_d5"] == rt.NA and r["answers"]["p11"] == "Entry 3 is wrong" and r["answers"]["f15"] == "No"
    assert "f13_cv" not in r["answers"]                   # skipped open question is not stored
    assert set(r["screens"]) == {rt.screen_id(s) for s in sc}


def test_newer_approved_profile_replaces_the_shown_one_until_the_first_answer(tmp_path, monkeypatch):
    store = Store(f"sqlite:///{tmp_path}/r.db")
    old = seed(store, "P01")
    at = new_app(tmp_path, monkeypatch, "P01")
    at.run()                                                  # opened before the new version existed
    assert store.get_rating("P01")["profile_id"] == old
    new = store.add_profile("P01", "resume-v3", True, "v3", {}, {}, pdf("Resume v3"))
    at.run()
    assert store.get_rating("P01")["profile_id"] == old      # not approved yet: still the old one
    store.approve_profile(new, True)
    at.run()
    assert store.get_rating("P01")["profile_id"] == new      # approved and nothing answered: switches
    store.save_rating_screen("P01", "profile.primary", {"profile_a1": 4}, 3)
    newer = store.add_profile("P01", "resume-v3", True, "again", {}, {}, pdf("Resume v3b"))
    store.approve_profile(newer, True)
    at.run()
    assert store.get_rating("P01")["profile_id"] == new      # answers exist: stays on the rated version


def test_admin_table_has_one_row_per_participant(tmp_path):
    import admin
    store = Store(f"sqlite:///{tmp_path}/r.db")
    pid = seed(store, "P01")
    store.start_rating("P01", rt.INSTRUMENT, "profile-first", pid)
    store.save_rating_screen("P01", "profile.primary", {"profile_a1": 7}, 5)
    t = admin.ratings_table(store)
    assert list(t["code"]) == ["P01"] and t.loc[0, "profile_a1"] == 7 and "cv_a1" in t.columns
