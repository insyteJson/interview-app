import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import autostar  # noqa: E402
from storage import Store  # noqa: E402

BASE = {"rule": "r", "phase_before": "start", "meta": None, "accepted": 0, "declined": 0, "audit": "",
        "audit_note": "", "turn": 0, "answer_raw": "", "question_shown": "q", "qid": "", "classification": None}


def incidents(n, roles):
    return [{"id": f"i{k:02d}", "position_label": f"Role {k % roles}", "dimension": "ability"} for k in range(n)]


def add(store, sid, n=4, roles=2, complete=True, cv=True):
    store.create_session(sid, {"status": "complete" if complete else "active", "incidents": incidents(n, roles),
                               "positions": [], "turn": 1, "phase": "done", "pos_idx": 0}, BASE)
    if cv:
        store.save_cv(sid, b"%PDF", {})


@pytest.fixture
def store(tmp_path):
    return Store(f"sqlite:///{tmp_path}/a.db")


@pytest.mark.parametrize("n,roles,complete,cv,expected", [
    (4, 2, True, True, True), (6, 3, True, True, True),
    (3, 2, True, True, False),           # too few situations
    (4, 1, True, True, False),           # all from one role
    (4, 2, False, True, False),          # interview not finished
    (4, 2, True, False, False),          # no CV
])
def test_rule(n, roles, complete, cv, expected):
    sess = {"status": "complete" if complete else "active", "state": {"incidents": incidents(n, roles)}}
    assert autostar.qualifies(sess, cv)[0] is expected


def test_cv_submit_stars_a_qualifying_session_once(store):
    add(store, "P10", cv=False)
    assert autostar.evaluate(store, "P10") is None                 # no CV yet: not judged
    store.save_cv("P10", b"%PDF", {})                              # what the CV step does, then the hook runs
    assert autostar.evaluate(store, "P10") is True
    assert "P10" in store.list_favorites()
    assert autostar.evaluate(store, "P10") is None                 # judged once


def test_a_removed_star_is_never_added_back(store):
    add(store, "P10")
    autostar.evaluate(store, "P10")
    store.set_favorite("P10", False)
    assert autostar.evaluate_all(store) == 0 and autostar.evaluate(store, "P10") is None
    assert "P10" not in store.list_favorites()


def test_sessions_finished_before_the_rule_are_left_alone_until_applied(store):
    add(store, "P01")                      # qualifies, finished before the rule existed
    add(store, "P02", n=2)                 # does not qualify
    add(store, "P03")
    store.set_favorite("P03", True)        # starred by hand earlier
    assert autostar.evaluate_all(store) == 0
    assert store.list_favorites() == {"P03"}
    assert {k: v["starred"] for k, v in store.list_auto_decisions().items() if k != autostar.BASELINE} == \
        {"P01": -1, "P02": -1, "P03": -1}
    add(store, "P04")                      # finishes after the rule was switched on
    assert autostar.evaluate_all(store) == 1 and "P04" in store.list_favorites()
    starred, checked = autostar.apply_to_existing(store)
    assert (starred, checked) == (1, 3)
    assert store.list_favorites() == {"P01", "P03", "P04"}        # P02 not starred, P03 stays


def test_first_cv_after_switch_on_is_judged_not_baselined(store):
    add(store, "P01")                      # existing
    add(store, "P02")                      # the session whose CV submit triggers the very first check
    assert autostar.evaluate(store, "P02") is True
    assert store.list_auto_decisions()["P01"]["starred"] == -1


def test_cv_hook_is_guarded_in_the_participant_flow():
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")).read()
    hook = src[src.index("store.save_cv(code"):src.index("st.session_state.pop(\"cv_result\", None)")]
    assert "autostar.evaluate(store, code)" in hook and "except Exception" in hook
