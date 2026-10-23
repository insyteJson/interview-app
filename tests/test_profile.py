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
                     "redacted name 'Anna'", "personal names still in the answers"):
        assert expected in text, expected
    assert "Excel" not in text and bad["tools"] == []                       # tools never block an entry
    assert "tool 'Excel' was dropped: not found in the answers" in warns


def test_tools_match_spelling_variants_and_word_forms():
    src = pg._norm("I did a mobilization of the joint and massaged the scar, then used Power BI")
    assert pg.tool_in_source("joint mobilisation", src) and pg.tool_in_source("scar massage", src)
    assert pg.tool_in_source("Power BI", src) and not pg.tool_in_source("ultrasound therapy", src)


def test_retry_shows_the_failed_attempt():
    fake = FakeClient([dict(GOOD, headline=" ".join(["word"] * 13)), GOOD])
    res = pg.generate_profile(STATE, TURNS, RULES, pg.ProfileGenerator("k", "m", client=fake))
    assert res["ok"]
    retry = fake.entry_calls()[1]["messages"][0]["content"]
    assert "Your previous entry was:" in retry and "word word word" in retry and "headline has 13 words" in retry
    assert "9 words or fewer" in retry


class FakeClient:
    """Answers entry calls from `replies` in order, purpose calls from `purposes` (default: echo the aim) and
    category calls from `categories` (default: no group)."""

    def __init__(self, replies, purposes=None, categories=None):
        self.replies, self.purposes, self.calls = list(replies), list(purposes or []), []
        self.categories = list(categories or [])
        self.messages = NS(create=self.create)

    def create(self, **kw):
        self.calls.append(kw)
        system = kw["system"][0]["text"]
        if system == pg.PURPOSE_SYSTEM:
            aim = json.loads(kw["messages"][0]["content"].split("\n\n")[0])["aim_answer"]
            r = self.purposes.pop(0) if self.purposes else {"purpose": aim, "personal_names": []}
        elif system == pg.CATEGORY_SYSTEM:
            r = self.categories.pop(0) if self.categories else {"categories": [], "personal_names": []}
        else:
            r = self.replies.pop(0)
        return NS(model="claude-opus-5-5", stop_reason="end_turn", usage=NS(input_tokens=900, output_tokens=200),
                  content=[NS(type="thinking", thinking=""), NS(type="text", text=json.dumps(r))])

    def entry_calls(self):
        return [c for c in self.calls if c["system"][0]["text"] == pg.SYSTEM]

    def category_calls(self):
        return [c for c in self.calls if c["system"][0]["text"] == pg.CATEGORY_SYSTEM]


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
    assert prof["profile_version"] == "resume-v3"
    assert prof["roles"][0]["display"] == "Team lead, Talk'n'Job" and prof["roles"][0]["period"] == "since 2024"
    assert prof["roles"][0]["tools_stated"] == ["Power BI", "Scrum"] and prof["roles"][0]["purpose"] == "Lead the team"
    x = prof["entries"][0]
    assert x["labels"] == ["Enterprising & Performing"] and x["tags"] == ["Self-initiated"] and x["tools"] == ["Power BI"]
    assert x["num"] == 1 and x["dimensions"] == ["integrity"]
    assert prof["categories"] == [] and not fake.category_calls()       # one role: nothing can recur across roles
    assert "fewer than two roles" in res["log"]["categories"]["skipped"]
    assert prof["label_stats"] == [{"label": "Enterprising & Performing", "situations": 1, "roles": 1,
                                    "role_indices": [0]}]
    assert prof["overview"]["rows"][0]["counts"] == {"ability": 0, "integrity": 1, "benevolence": 0}
    assert [a["attempt"] for a in res["log"]["incidents"][0]["attempts"]] == [1, 2]


def test_generate_gives_up_after_three_failed_attempts():
    fake = FakeClient([dict(GOOD, personal_names=["Bob"])] * 3)
    res = pg.generate_profile(STATE, TURNS, RULES, pg.ProfileGenerator("k", "m", client=fake))
    assert not res["ok"] and res["profile"]["entries"] == []


def test_request_matches_sdk_signature_and_schema_rules():
    for kind in ("entry", "purpose", "categories"):
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
    _walk(pg.CATEGORY_SCHEMA)


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


CAT = {"categories": [{"statement": "Steps in for colleagues who need help", "members": [2, 3],
                       "summary": "In two roles, the candidate stepped in for colleagues: teaching an apprentice a lab "
                                  "method [2] and taking over a sick co-worker's class [3]."}],
       "personal_names": []}


def _p01_profile(categories=(CAT,)):
    fake = FakeClient([_entry("Worked late with the team to meet a regulatory deadline",
                              ["Organising & Executing", "Enterprising & Performing"]),
                       dict(_entry("Trained a junior colleague in a lab method", ["Organising & Executing"]),
                            label_evidence=[{"name": "Organising & Executing", "quote": "the samples were finished"}]),
                       _entry("Took over a colleague's class at short notice", [])], categories=list(categories))
    turns = _p01_turns()
    for t in turns:   # every answer contains the evidence quotes used above
        t["answer_raw"] += ". stayed two hours longer. the samples were finished"
    res = pg.generate_profile(P01_STATE, turns, [], pg.ProfileGenerator("k", "m", client=fake), merges=[["i01", "i02"]])
    return res, fake


def _pdf_text(pdf):
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    lig = {"\xa0": " ", "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl"}
    return ["".join(lig.get(ch, ch) for ch in p.get_text()) for p in doc]


def test_resume_v3_merges_numbers_groups_and_renders_the_template():
    res, fake = _p01_profile()
    assert res["ok"], res["log"]
    prof = res["profile"]
    assert len(fake.entry_calls()) == 3 and len(fake.category_calls()) == 1   # 4 incidents, one merged pair
    sent = json.loads(fake.category_calls()[0]["messages"][0]["content"])
    assert [x["n"] for x in sent["situations"]] == [1, 2, 3]
    assert [e["num"] for e in prof["entries"]] == [1, 2, 3]
    assert [e["id"] for e in prof["entries"]] == ["i01", "i03", "i04"]         # by role, then interview order
    assert prof["entries"][0]["dimensions"] == ["ability", "integrity"]
    c = prof["categories"][0]
    assert c["situations"] == 2 and c["roles"] == 2 and c["themes"] == ["benevolence"] and c["members"] == [2, 3]
    ov = prof["overview"]
    assert ov["n_situations"] == 3 and ov["rows"][0]["counts"] == {"ability": 1, "integrity": 1, "benevolence": 1}
    stats = {x["label"]: x for x in prof["label_stats"]}
    assert stats["Organising & Executing"]["situations"] == 2 and stats["Organising & Executing"]["roles"] == 1
    pages = _pdf_text(pg.render_pdf(prof))
    assert prof["layout"] == "summary on page 1" and len(pages) == 2
    p1, p2 = pages
    for expected in ("Where behaviour was shown", "Roles with at least one situation", "Kinds of behaviour shown",
                     "Organising & Executing", "2 situations", "Summary", "Steps in for colleagues who need help",
                     "2 situations · 2 roles", "[2]", "No situation given", "Purpose: I analyse water samples"):
        assert expected in p1, expected
    for expected in ("Situations", "[1]", "Worked late with the team to meet a regulatory deadline.",
                     "Doing the work + Standing by standards", "Situation:", "Result:"):
        assert expected in p2, expected
    assert "Situation:" not in p1


def test_no_recurring_behaviour_is_stated_not_hidden():
    res, _ = _p01_profile(categories=({"categories": [], "personal_names": []},))
    assert res["ok"] and res["profile"]["categories"] == []
    assert "No kind of behaviour was described in situations from two different roles" in _pdf_text(
        pg.render_pdf(res["profile"]))[0]


def test_categories_retry_with_feedback_then_block_the_profile():
    bad = {"categories": [{"statement": "Always helpful", "members": [1, 2],
                           "summary": "The candidate helped twice [1]."}], "personal_names": []}
    res, fake = _p01_profile(categories=(bad, bad, bad))
    assert not res["ok"] and len(fake.category_calls()) == 3
    retry = fake.category_calls()[1]["messages"][0]["content"]
    assert "Your previous answer was:" in retry and "trait or frequency words" in retry
    assert "failed the checks" in res["log"]["categories"]["skipped"]


def test_category_checks():
    entries = [{"num": 1, "role_index": 0, "dimensions": ["ability"]}, {"num": 2, "role_index": 0, "dimensions": ["ability"]},
               {"num": 3, "role_index": 1, "dimensions": ["integrity"]}]
    ok = [{"statement": "Raises problems with the person responsible", "members": [1, 3],
           "summary": "In two roles, the candidate raised problems with the person responsible [1] [3]."}]
    assert pg.check_categories(ok, [], entries, []) == ([], [])
    def fails(cats, names=()):
        return " | ".join(pg.check_categories(cats, list(names), entries, [])[0])
    assert "2 different roles" in fails([dict(ok[0], members=[1, 2], summary="Twice [1] [2].")])
    assert "cite each member exactly once" in fails([dict(ok[0], summary="Raised problems [1].")])
    assert "unknown situation numbers [7]" in fails([dict(ok[0], members=[1, 3, 7])])
    assert "trait or frequency words: consistently" in fails([dict(ok[0], statement="Consistently raises problems")])
    assert "contains a number" in fails([dict(ok[0], statement="Raised 3 problems")])
    assert "number other than the citations" in fails([dict(ok[0], summary="It took 2 hours [1] [3].")])
    assert "used more than once" in fails([ok[0], dict(ok[0], statement="Helps others")])
    assert "personal names" in fails(ok, ["Tom"])


def test_headline_checks_for_behaviour_level():
    src = "I worked until 1:00 AM before the deadline, the boss now sees live reporting"
    for headline, expected in (("Collected 70 samples in one day", "contains a number"),
                               ("Consistently went beyond the call of duty", "trait, frequency"),
                               ("According to the candidate saved the project", "according")):
        fails, _ = pg.check_entry(dict(GOOD, headline=headline, labels=[], tags=[]), src, [])
        assert any(expected in f for f in fails), (headline, fails)
    _, warns = pg.check_entry(dict(GOOD, labels=[], tags=[], result="The candidate described it as a success."), src, [])
    assert any("own judgement" in w for w in warns)


def test_pdf_layout_single_role_and_overflow_fallback():
    res = pg.generate_profile(STATE, TURNS, RULES, pg.ProfileGenerator("k", "m", client=FakeClient([GOOD])))
    pages = _pdf_text(pg.render_pdf(res["profile"]))
    assert len(pages) == 2
    for expected in ("Behavioural resume", "Team lead, Talk'n'Job", "since 2024", "Power BI, Scrum",
                     "Purpose: Lead the team", "Enterprising & Performing", "Candidate P01"):
        assert expected in pages[0], expected
    for hidden in ("integrity", "fallback", "Anna", "Looking back"):
        assert hidden.lower() not in " ".join(pages).lower(), hidden
    # many roles: page 1 overflows, so the summary moves to page 2 (fills can never leak onto later pages)
    prof = json.loads(json.dumps(res["profile"]))
    role = prof["roles"][0]
    prof["roles"] = [dict(role, display=f"Role {chr(65 + k)}", purpose="Lead the team " * 6) for k in range(9)]
    prof["overview"] = pg.build_overview(prof["roles"], prof["entries"])
    pg.render_pdf(prof)
    assert prof["layout"].startswith("summary on page 2")
