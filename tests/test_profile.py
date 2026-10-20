import inspect
import json
import os
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymupdf  # noqa: E402
from anthropic.resources.messages import Messages  # noqa: E402

import profile_gen as pg  # noqa: E402


def T(turn, qid, answer="", label=None, q="question"):
    return {"turn": turn, "qid": qid, "answer_raw": answer, "question_shown": q,
            "classification": {"label": label} if label else None}


# trait answer -> trait redirect -> trait again -> fallback prompt -> answer -> outcome -> accepted at turn 14
TURNS = [
    T(10, "P_INTEGRITY_1", "move on", "DECLINE", q="In your role as team lead, think of a moment ..."),
    T(11, "Q_TRAIT", "I always do the right thing", "TRAIT", q="Can you walk me through one specific instance?"),
    T(12, "P_INTEGRITY_2", "hard to pinpoint", "TRAIT", q="think of any moment where you stuck to a standard ..."),
    T(13, "Q_OUT", "Anna asked me, so I worked until 1:00 AM in Power BI to finish before the deadline on my own "
                   "initiative", "ANSWER", q="You said ... What happened as a result?"),
    T(14, "Q_REFL", "the boss now sees live reporting of sign-ups", "ANSWER", q="Looking back ...?"),
]
INC = {"id": "i01", "position_label": "Team lead at Talk'n'Job", "position_type": "job", "dimension": "integrity",
       "level": "fallback", "reflection": "no", "turn_accepted": 14}
POS = {"item": "team lead", "title": "Team lead", "company": "Talk'n'Job", "period": "since 2024", "type": "job",
       "label": "Team lead at Talk'n'Job", "aim": "Lead the team", "tools": ["Power BI", "Scrum"]}
STATE = {"session_id": "P01", "positions": [POS], "incidents": [INC], "status": "complete"}
RULES = pg.parse_rules("Anna => a colleague")


def test_incident_exchanges_start_at_the_answer_that_opened_the_moment():
    ex = pg.incident_exchanges(INC, TURNS)
    assert [e["answer"][:9] for e in ex] == ["Anna aske", "the boss "]
    assert ex[0]["question"].startswith("think of any moment")


def test_rules_whole_word_case_insensitive_and_suggestions():
    assert pg.apply_rules("ANNA and Annabel", RULES) == "a colleague and Annabel"
    assert pg.parse_rules("Weber\n# comment") == [("Weber", "[name removed]")]
    sugg = dict(pg.suggest_identifiers(["I told Anna Schmidt about it", "Yeah I mean"]))
    assert "Anna Schmidt" in sugg and "Yeah" not in sugg


GOOD = {"headline": "Worked late to finish the reporting before the deadline",
        "situation": "A colleague asked for the reporting, and a deadline applied.",
        "result": "The candidate worked until 1:00 AM; the boss now sees live reporting of sign-ups.",
        "labels": ["Enterprising & Performing"],
        "label_evidence": [{"name": "Enterprising & Performing", "quote": "worked until 1:00 AM"}],
        "tags": ["Under time pressure", "Self-initiated"],
        "tag_evidence": [{"name": "Under time pressure", "quote": "before the deadline"},
                         {"name": "Self-initiated", "quote": "on my own initiative"}],
        "tools": ["Power BI"], "personal_names": []}
SOURCE = ("a colleague asked me, so I worked until 1:00 AM in Power BI to finish before the deadline on my own "
          "initiative the boss now sees live reporting of sign-ups")


def test_checks_pass_good_entry():
    assert pg.check_entry(pg._normalise(json.loads(json.dumps(GOOD))), SOURCE, RULES) == ([], [])


def test_checks_catch_each_failure():
    bad = dict(GOOD, headline=" ".join(["word"] * 13), situation="At 3 AM Anna excellently asked.",
               result=" ".join(["x"] * 36),
               labels=["Leading & Deciding", "Organising & Executing", "Adapting & Coping"],
               label_evidence=[], tag_evidence=[{"name": "Under time pressure", "quote": "a very tight deadline"}],
               tools=["Excel"], personal_names=["Anna"])
    fails, warns = pg.check_entry(bad, SOURCE, RULES)
    text = " | ".join(fails)
    for expected in ("headline has 13 words", "result has 36 words", "number '3'", "3 labels",
                     "label 'Leading & Deciding' has no verbatim evidence", "tag 'Under time pressure' has no verbatim",
                     "tool 'Excel' does not appear", "redacted name 'Anna'", "personal names still in the answers"):
        assert expected in text, expected
    assert warns == ["evaluative word 'excellently' is not in the answers"] or not warns


class FakeClient:
    """Answers entry calls from `replies` in order, and purpose calls from `purposes` (default: echo the aim)."""

    def __init__(self, replies, purposes=None):
        self.replies, self.purposes, self.calls = list(replies), list(purposes or []), []
        self.messages = NS(create=self.create)

    def create(self, **kw):
        self.calls.append(kw)
        if kw["system"][0]["text"] == pg.PURPOSE_SYSTEM:
            aim = json.loads(kw["messages"][0]["content"].split("\n\n")[0])["aim_answer"]
            r = self.purposes.pop(0) if self.purposes else {"purpose": aim, "personal_names": []}
        else:
            r = self.replies.pop(0)
        return NS(model="claude-opus-5-5", stop_reason="end_turn", usage=NS(input_tokens=900, output_tokens=200),
                  content=[NS(type="thinking", thinking=""), NS(type="text", text=json.dumps(r))])

    def entry_calls(self):
        return [c for c in self.calls if c["system"][0]["text"] == pg.SYSTEM]


def test_generate_retries_failed_check_and_builds_resume():
    fake = FakeClient([dict(GOOD, headline=" ".join(["x"] * 20)),
                       dict(GOOD, labels=["enterprising & performing"], tags=["self-initiated"],
                            tag_evidence=[{"name": "Self-initiated", "quote": "on my own initiative"}])])
    gen = pg.ProfileGenerator("k", "claude-opus-5-5", client=fake)
    res = pg.generate_profile(STATE, TURNS, RULES, gen)
    assert res["ok"]
    calls = fake.entry_calls()
    assert len(calls) == 2 and "failed these checks" in calls[1]["messages"][0]["content"]
    sent = calls[0]["messages"][0]["content"]
    assert "Anna" not in sent and "a colleague" in sent and "Talk'n'Job" in sent   # only personal names removed
    prof = res["profile"]
    assert prof["profile_version"] == "resume-v2"
    assert prof["roles"][0]["display"] == "Team lead, Talk'n'Job" and prof["roles"][0]["period"] == "since 2024"
    assert prof["roles"][0]["tools_stated"] == ["Power BI", "Scrum"] and prof["roles"][0]["purpose"] == "Lead the team"
    x = prof["entries"][0]
    assert x["labels"] == ["Enterprising & Performing"] and x["tags"] == ["Self-initiated"] and x["tools"] == ["Power BI"]
    assert prof["themes"]["integrity"] == [{"id": "i01", "full": True}]
    assert prof["label_stats"] == [{"label": "Enterprising & Performing", "situations": 1, "roles": 1,
                                    "role_indices": [0]}]
    assert prof["overview"]["rows"][0]["counts"] == {"ability": 0, "integrity": 1, "benevolence": 0}
    assert [a["attempt"] for a in res["log"]["incidents"][0]["attempts"]] == [1, 2]


def test_generate_gives_up_after_three_failed_attempts():
    fake = FakeClient([dict(GOOD, personal_names=["Bob"])] * 3)
    res = pg.generate_profile(STATE, TURNS, RULES, pg.ProfileGenerator("k", "m", client=fake))
    assert not res["ok"] and res["profile"]["entries"] == []


def test_request_matches_sdk_signature_and_schema_rules():
    for kind in ("entry", "purpose"):
        kw = pg.ProfileGenerator("k", "claude-opus-5-5", client=object()).request_kwargs({"x": 1}, kind=kind)
        inspect.signature(Messages.create).bind(None, **kw)
        for forbidden in ("temperature", "top_p", "top_k", "tool_choice", "thinking"):
            assert forbidden not in kw
        assert kw["output_config"]["format"]["type"] == "json_schema"
    schema = json.dumps(pg.SCHEMA)
    assert "rationale" not in schema and "reasoning" not in schema


def _walk(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False
            assert set(node["required"]) == set(node["properties"])
        for v in node.values():
            _walk(v)
    elif isinstance(node, list):
        for v in node:
            _walk(v)


def test_schema_objects_are_closed_and_fully_required():
    _walk(pg.SCHEMA)
    _walk(pg.PURPOSE_SCHEMA)


# ------------------------------------------------------------------ resume-v2

def test_purpose_checks():
    aim = "I analyse water samples so that hospitals meet the hygiene regulations"
    assert pg.check_purpose("Analyse water samples to meet hygiene regulations", [], aim, []) == ([], [])
    fails, _ = pg.check_purpose("Drive strategic excellence across global innovation teams", [], aim, [])
    assert any("content words" in f for f in fails)
    fails, _ = pg.check_purpose(" ".join(["analyse"] * 16), [], aim, [])
    assert any("16 words" in f for f in fails)
    fails, _ = pg.check_purpose("Analyse 70 water samples", ["Anna"], aim, RULES)
    assert any("number '70'" in f for f in fails) and any("personal names" in f for f in fails)


def test_purpose_retries_then_omits_without_failing_the_profile():
    bad = {"purpose": "Drive strategic excellence across global innovation teams", "personal_names": []}
    fake = FakeClient([GOOD], purposes=[bad, bad, bad])
    res = pg.generate_profile(STATE, TURNS, RULES, pg.ProfileGenerator("k", "m", client=fake))
    assert res["ok"] and res["profile"]["roles"][0]["purpose"] is None
    plog = res["log"]["purposes"][0]
    assert len(plog["attempts"]) == 3 and "failed the checks" in plog["skipped"]


def test_purpose_skipped_without_aim_and_empty_purpose():
    state = dict(STATE, positions=[dict(POS, aim=None)])
    fake = FakeClient([GOOD])
    res = pg.generate_profile(state, TURNS, RULES, pg.ProfileGenerator("k", "m", client=fake))
    assert res["profile"]["roles"][0]["purpose"] is None and len(fake.calls) == 1
    fake = FakeClient([GOOD], purposes=[{"purpose": "", "personal_names": []}])
    res = pg.generate_profile(STATE, TURNS, RULES, pg.ProfileGenerator("k", "m", client=fake))
    assert res["profile"]["roles"][0]["purpose"] is None


# A P01-like session: two roles, the same event told under two themes in the lab.
LAB = {"title": "Biological laboratory technician", "company": "Universitätsmedizin Göttingen", "period": "since 05/2024",
       "type": "job", "category": "job", "employment": "Part-time",
       "label": "Biological laboratory technician at Universitätsmedizin Göttingen",
       "aim": "I analyse water samples so that hospitals meet the hygiene regulations", "tools": ["microbiology"]}
DANCE = {"title": "Dance teacher", "company": "Tanzschule Krebs", "period": "since 06/2020", "type": "job",
         "category": "job", "employment": "Student job / mini-job", "label": "Dance teacher at Tanzschule Krebs",
         "aim": "I teach hip hop classes and choreographies", "tools": ["choreography"]}
GYM = {"title": "Gym", "company": None, "period": None, "type": "non-work", "category": "hobby", "label": "Gym",
       "aim": "stay fit", "tools": []}


def _inc(i, pos, dim, turn, s, a, o):
    return {"id": i, "position_label": pos["label"], "position_type": pos["type"], "dimension": dim, "level": "primary",
            "situation": s, "action": a, "outcome": o, "reflection": "no", "turn_accepted": turn}


P01_INCS = [
    _inc("i01", LAB, "ability", 2, "Near Christmas many samples had to be processed because of hygiene regulations",
         "stayed two hours longer to finish the samples", "the team finished all samples before Christmas"),
    _inc("i02", LAB, "integrity", 4, "Before Christmas the samples needed to be finished to fulfil the regulations",
         "I stayed two extra hours at work", "the samples were finished, a ten hour day"),
    _inc("i03", LAB, "benevolence", 6, "the apprentice needed to learn a microbiology method",
         "I taught her the method next to my own tasks", "she now performs it alone"),
    _inc("i04", DANCE, "benevolence", 8, "a co-worker suddenly fell ill", "I covered her class",
         "the class took place"),
]


def _p01_turns():
    turns = []
    for inc in P01_INCS:
        t = inc["turn_accepted"]
        turns += [T(t - 1, "P_X_1", "skip", "DECLINE", q="In your role ..."),
                  T(t, "Q_OUT", f"{inc['situation']}. {inc['action']}. {inc['outcome']}", "ANSWER", q="Q?")]
    turns[0]["qid"] = "Q_AIM"
    for k in range(1, len(turns), 2):
        turns[k - 1]["qid"] = "P_ABILITY_1"
    return turns


P01_STATE = {"session_id": "P01", "positions": [LAB, DANCE, GYM], "incidents": P01_INCS, "status": "complete"}


def _entry(headline, labels):
    ev = [{"name": lab, "quote": "the samples were finished" if "Organis" in lab else "stayed two hours longer"}
          for lab in labels]
    return {"headline": headline, "situation": "Samples had to be processed before Christmas.",
            "result": "The samples were finished.", "labels": labels, "label_evidence": ev, "tags": [],
            "tag_evidence": [], "tools": [], "personal_names": []}


def test_duplicate_candidates_flag_the_same_event_only():
    cands = pg.duplicate_candidates(P01_STATE)
    assert {(c["a"], c["b"]) for c in cands} == {("i01", "i02"), ("i01", "i03"), ("i02", "i03")}  # same role only
    top = cands[0]
    assert (top["a"], top["b"]) == ("i01", "i02") and top["likely"]
    assert not any(c["likely"] for c in cands[1:])


def test_merge_groups_ignore_cross_role_and_unknown_pairs():
    groups = pg.merge_groups(P01_INCS, [["i02", "i01"], ["i01", "i04"], ["i01", "zz"]])
    assert [[i["id"] for i in g] for g in groups] == [["i01", "i02"], ["i03"], ["i04"]]


def test_merged_event_is_written_once_counted_once_and_cross_listed():
    fake = FakeClient([_entry("Stayed two hours late to finish regulated samples",
                              ["Organising & Executing", "Enterprising & Performing"]),
                       dict(_entry("Taught the apprentice a microbiology method", ["Organising & Executing"]),
                            label_evidence=[{"name": "Organising & Executing", "quote": "the samples were finished"}]),
                       _entry("Covered a sick co-worker's class", [])])
    turns = _p01_turns()
    for t in turns:   # every answer contains the evidence quotes used above
        t["answer_raw"] += ". stayed two hours longer. the samples were finished"
    res = pg.generate_profile(P01_STATE, turns, [], pg.ProfileGenerator("k", "m", client=fake), merges=[["i01", "i02"]])
    assert res["ok"], res["log"]
    prof = res["profile"]
    calls = fake.entry_calls()
    assert len(calls) == 3                                    # 4 incidents, one merged pair
    first = json.loads(calls[0]["messages"][0]["content"])
    assert first["note"] == pg.MERGE_NOTE and len(first["exchange"]) == 2
    assert res["log"]["merges"] == [["i01", "i02"]]
    merged = next(e for e in prof["entries"] if e["members"] == ["i01", "i02"])
    assert merged["dimensions"] == ["ability", "integrity"]
    assert {"id": "i01", "full": True} in prof["themes"]["ability"]
    assert {"id": "i01", "full": False} in prof["themes"]["integrity"]
    ov = prof["overview"]
    assert ov["n_situations"] == 3 and ov["n_roles"] == 3
    assert ov["rows"][0]["counts"] == {"ability": 1, "integrity": 1, "benevolence": 1}
    assert ov["rows"][2]["situations"] == 0                   # gym: no situation
    assert ov["roles_with"] == {"ability": 1, "integrity": 1, "benevolence": 2}
    stats = {s["label"]: s for s in prof["label_stats"]}
    assert stats["Organising & Executing"]["situations"] == 2 and stats["Organising & Executing"]["roles"] == 1
    # ordering: entries sharing the more widespread label first
    assert prof["entries"][0]["labels"] and not prof["entries"][-1]["labels"]
    pdf = pg.render_pdf(prof)
    text = "\n".join(p.get_text() for p in pymupdf.open(stream=pdf, filetype="pdf")).replace("\xa0", " ").replace("\ufb01", "fi")
    for expected in ("Where behaviour was shown", "Roles with at least one situation", "1 of 3", "Kinds of behaviour shown",
                     "In one role only", "Also here:", "counted once", "also counts for Standing by standards",
                     "No situation given", "Purpose: I analyse water samples", "Doing the work", "Helping others"):
        assert expected in text, expected
    assert text.count("Stayed two hours late to finish regulated samples") == 2   # full entry + stub


def test_pdf_layout():
    res = pg.generate_profile(STATE, TURNS, RULES, pg.ProfileGenerator("k", "m", client=FakeClient([GOOD])))
    pdf = pg.render_pdf(res["profile"])
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    text = "\n".join(p.get_text() for p in doc)
    for expected in ("Behavioural resume", "Where behaviour was shown", "Team lead, Talk'n'Job", "since 2024",
                     "Power BI, Scrum", "Purpose: Lead the team", "Standing by standards", "Enterprising & Performing",
                     "Situation:", "Result:", "Candidate P01"):
        assert expected in text, expected
    for hidden in ("integrity", "fallback", "Anna", "Looking back"):
        assert hidden.lower() not in text.lower(), hidden
    assert doc.page_count == 1
