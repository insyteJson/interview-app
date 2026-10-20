import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine  # noqa: E402
from engine import CLOSE_ABORT, CLOSE_COMPLETE, Q, T  # noqa: E402


def A(**kw):
    d = {"label": "ANSWER"}
    d.update(kw)
    return d


def run(state, answer, cls):
    return engine.step(state, answer, cls)


def started(positions):
    """Role form submitted; returns the state at the aim question of the first position."""
    s, r = engine.start(engine.new_state("T", "m"))
    assert r["output"].startswith(engine.BRIEFING)
    assert r["output"].endswith(Q["Q_ROLES"]) and s["phase"] == "form"
    return engine.set_positions(s, positions)


def aim_tools(s, tools=("Python",)):
    """Answer the aim and the tools question; returns the state at the first dimension prompt."""
    s, r = run(s, "purpose", A())
    assert r["qid"] in ("Q_TOOLS_JOB", "Q_TOOLS_ACT")
    return run(s, "tools", A(tools=list(tools)))


JOB = {"item": "Job: IT team lead", "title": "IT team lead", "company": "a startup", "label": "IT team lead at a startup",
       "type": "job", "category": "job"}
HOBBY = {"item": "Hobby: gravel cyclist", "title": "recreational gravel cyclist", "company": None,
         "label": "recreational gravel cyclist", "type": "non-work", "category": "hobby"}

FULL = dict(situation="old product did not sell", action="I rebuilt the roadmap",
            outcome="we released it in October", outcome_definite=True)


def test_listing_orders_jobs_first_and_asks_aim():
    s, r = started([HOBBY, JOB])
    assert [p["label"] for p in s["positions"]] == [JOB["label"], HOBBY["label"]]
    assert r["output"] == T["T_FIRSTPOS"] + "\n\n" + Q["Q_AIM"].format(label=JOB["label"])


def test_form_turn_counts_and_chat_answers_are_ignored_until_the_form_is_sent():
    s, _ = engine.start(engine.new_state("T", "m"))
    s2, r = engine.step(s, "hello", {"label": "ANSWER"})
    assert r["rule"] == "form-pending" and s2["turn"] == 0 and s2["phase"] == "form"
    s3, r = engine.set_positions(s, [JOB])
    assert s3["turn"] == 1 and r["rule"] == "positions-form" and r["qid"] == "Q_AIM"
    try:
        engine.set_positions(s3, [JOB])
    except ValueError:
        pass
    else:
        raise AssertionError("a second form submission must be refused")


def test_max_positions_drops_the_rest_after_jobs_first():
    s, _ = engine.start(engine.new_state("T", "m", max_positions=1))
    s, _ = engine.set_positions(s, [HOBBY, JOB])
    assert [p["label"] for p in s["positions"]] == [JOB["label"]]
    assert [p["label"] for p in s["positions_dropped"]] == [HOBBY["label"]]


def test_component_order_and_acceptance():
    s, _ = started([JOB])
    s, r = aim_tools(s)
    assert r["output"].endswith(Q["P_ABILITY_1"].format(label=JOB["label"]))
    # moment with action and outcome but no situation -> situation must come first
    s, r = run(s, "moment", A(action="I planned it", outcome="released", outcome_definite=True,
                             restatement="you planned it"))
    assert r["output"].startswith(T["T_COMP"])
    assert r["output"].endswith(Q["Q_SIT"])
    s, r = run(s, "the old product did not sell", A(situation="the old product did not sell",
                                                    restatement="the old product did not sell"))
    assert r["rule"] == "accept"
    assert r["output"] == T["T_REFL"] + "\n\n" + Q["Q_REFL"]
    assert s["counts"]["ability"] == 1


def test_judgement_outcome_rechecked_once_then_vague():
    s, _ = started([JOB])
    s, _ = aim_tools(s)
    s, r = run(s, "moment", A(situation="x", action="y", outcome="it was a success", outcome_definite=False))
    assert r["rule"] == "outcome-recheck"
    assert r["output"].endswith(Q["Q_OUT"])
    s, r = run(s, "it was great", A(outcome="it was great", outcome_definite=False))
    assert r["output"] == Q["Q_DIFF"]
    s, r = run(s, "no", {"label": "DECLINE"})
    # job, primary declined -> fallback, no transition
    assert r["output"] == Q["P_ABILITY_2"].format(label=JOB["label"])


def test_job_decline_goes_primary_fallback_next():
    s, _ = started([JOB])
    s, _ = aim_tools(s)
    s, r = run(s, "no", {"label": "DECLINE"})
    assert r["output"] == Q["P_ABILITY_2"].format(label=JOB["label"])
    s, r = run(s, "skip", {"label": "DECLINE"})
    assert r["output"] == T["T_MOMENT"] + "\n\n" + Q["P_INTEGRITY_1"].format(label=JOB["label"])
    assert s["incidents"] == []


def test_nonwork_has_no_fallback_and_no_continuation():
    s, _ = started([HOBBY])
    s, _ = aim_tools(s)
    s, r = run(s, "no", {"label": "DECLINE"})
    assert r["output"].endswith(Q["P_INTEGRITY_1"].format(label=HOBBY["label"]))
    s, r = run(s, "moment", A(**FULL))
    assert r["rule"] == "accept"
    s, r = run(s, "nothing", A())
    assert r["output"].endswith(Q["P_BENEVOLENCE_1"].format(label=HOBBY["label"]))


def test_continuation_ceiling_three():
    s, _ = started([JOB])
    s, _ = aim_tools(s)
    s, r = run(s, "moment", A(**FULL))
    assert r["rule"] == "accept"
    for i in range(2):
        s, r = run(s, "nothing", A())
        assert r["output"] == Q["Q_CONT"].format(label=JOB["label"])
        s, r = run(s, "yes another one", A())
        assert r["output"].endswith(Q["Q_SIT"]) and r["output"].startswith(T["T_COMP"])
        s, r = run(s, "details", A(**FULL))
        assert r["rule"] == "accept"
        assert s["incidents"][-1]["level"] == "continuation"
    s, r = run(s, "nothing", A())  # third reflection -> ceiling reached
    assert s["counts"]["ability"] == 3
    assert r["output"].endswith(Q["P_INTEGRITY_1"].format(label=JOB["label"]))


def test_trait_redirect_then_decline():
    s, _ = started([JOB])
    s, _ = aim_tools(s)
    s, r = run(s, "I'm reliable", {"label": "TRAIT"})
    assert r["output"] == Q["Q_TRAIT"]
    s, r = run(s, "I just am", {"label": "TRAIT"})
    assert r["output"] == Q["P_ABILITY_2"].format(label=JOB["label"])


def test_complaint_never_ends_or_restarts():
    s, _ = started([JOB])
    s, _ = aim_tools(s)
    before = (s["pos_idx"], s["dimension"], s["level"])
    s, r = run(s, "we already covered that", {"label": "COMPLAINT"})
    assert r["output"].startswith(Q["P_ABILITY_1"].format(label=JOB["label"]))
    assert engine.CLAR_SAME in r["output"]
    assert (s["pos_idx"], s["dimension"], s["level"]) == before
    s, r = run(s, "again, covered", {"label": "COMPLAINT"})
    assert r["output"] == Q["Q_STOP"]
    s, r = run(s, "no, continue", {"label": "ANSWER", "confirm": "no"})
    assert r["output"] == Q["P_ABILITY_1"].format(label=JOB["label"])
    assert s["status"] == "interview"


def test_stop_confirmed_aborts():
    s, _ = started([JOB])
    s, r = run(s, "I want to stop", {"label": "STOP"})
    assert r["output"] == Q["Q_STOP"]
    s, r = run(s, "yes", {"label": "ANSWER", "confirm": "yes"})
    assert s["status"] == "aborted" and r["output"] == CLOSE_ABORT


def test_full_run_completes():
    s, _ = started([HOBBY])
    s, _ = aim_tools(s)
    for _ in range(3):
        s, r = run(s, "no", {"label": "DECLINE"})
    assert s["status"] == "complete" and r["output"] == CLOSE_COMPLETE


def test_restatement_must_use_participant_words():
    ans = "we rebuilt the whole product for restaurants in one month"
    assert engine.valid_restatement("you rebuilt the whole product for restaurants", ans)
    assert engine.valid_restatement("you showed great leadership and vision", ans) is None
    assert engine.valid_restatement(" ".join(["word"] * 30), ans) is None


def test_misunderstood_repeats_with_clause():
    s, _ = started([JOB])
    s, _ = aim_tools(s)
    s, r = run(s, "what do you mean?", {"label": "MISUNDERSTOOD"})
    assert r["output"] == Q["P_ABILITY_1"].format(label=JOB["label"]) + " " + engine._OCCASION


def test_different_moment_restarts_components():
    s, _ = started([JOB])
    s, _ = aim_tools(s)
    s, r = run(s, "one time at work", A(situation="at work"))
    assert r["output"].endswith(Q["Q_ACT"])
    s, r = run(s, "hmm, not sure", {"label": "VAGUE"})
    assert r["output"] == Q["Q_DIFF"]
    s, r = run(s, "yes: when the server failed I restored backups", A(situation="the server failed",
                                                                         action="I restored backups"))
    assert s["incident"]["s"] == "the server failed"  # old situation discarded
    assert r["output"].endswith(Q["Q_OUT"]) and r["output"].startswith(T["T_COMP"])


def test_aim_then_tools_question_with_company_label():
    pos = dict(JOB, title="Team lead", company="Talk'n'Job", label="Team lead at Talk'n'Job")
    s, r = started([pos])
    assert r["output"].endswith(Q["Q_AIM"].format(label="Team lead at Talk'n'Job"))
    s, r = run(s, "lead the team", A())
    assert r["output"] == Q["Q_TOOLS_JOB"]
    s, r = run(s, "Power Apps, Scrum", A(tools=["Power Apps", "Scrum"]))
    assert s["positions"][0]["tools"] == ["Power Apps", "Scrum"]
    assert r["output"].endswith(Q["P_ABILITY_1"].format(label="Team lead at Talk'n'Job"))


def test_tools_decline_and_activity_wording():
    s, _ = started([HOBBY])
    s, r = run(s, "fun", A())
    assert r["output"] == Q["Q_TOOLS_ACT"]
    s, r = run(s, "skip", {"label": "DECLINE"})
    assert s["positions"][0]["tools"] == [] and r["output"].endswith(Q["P_ABILITY_1"].format(label=HOBBY["label"]))
