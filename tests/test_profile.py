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
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.messages = NS(create=self.create)

    def create(self, **kw):
        self.calls.append(kw)
        r = self.replies.pop(0)
        return NS(model="claude-opus-5-5", stop_reason="end_turn", usage=NS(input_tokens=900, output_tokens=200),
                  content=[NS(type="thinking", thinking=""), NS(type="text", text=json.dumps(r))])


def test_generate_retries_failed_check_and_builds_resume():
    fake = FakeClient([dict(GOOD, headline=" ".join(["x"] * 20)),
                       dict(GOOD, labels=["enterprising & performing"], tags=["self-initiated"],
                            tag_evidence=[{"name": "Self-initiated", "quote": "on my own initiative"}])])
    gen = pg.ProfileGenerator("k", "claude-opus-5-5", client=fake)
    res = pg.generate_profile(STATE, TURNS, RULES, gen)
    assert res["ok"]
    assert len(fake.calls) == 2 and "failed these checks" in fake.calls[1]["messages"][0]["content"]
    sent = fake.calls[0]["messages"][0]["content"]
    assert "Anna" not in sent and "a colleague" in sent and "Talk'n'Job" in sent   # only personal names removed
    prof = res["profile"]
    assert prof["roles"][0]["display"] == "Team lead, Talk'n'Job" and prof["roles"][0]["period"] == "since 2024"
    assert prof["roles"][0]["tools_stated"] == ["Power BI", "Scrum"]
    x = prof["themes"]["integrity"][0]
    assert x["labels"] == ["Enterprising & Performing"] and x["tags"] == ["Self-initiated"] and x["tools"] == ["Power BI"]
    assert prof["label_counts"] == [("Enterprising & Performing", 1)]
    assert [a["attempt"] for a in res["log"]["incidents"][0]["attempts"]] == [1, 2]


def test_generate_gives_up_after_three_failed_attempts():
    fake = FakeClient([dict(GOOD, personal_names=["Bob"])] * 3)
    res = pg.generate_profile(STATE, TURNS, RULES, pg.ProfileGenerator("k", "m", client=fake))
    assert not res["ok"] and res["profile"]["themes"]["integrity"] == []


def test_request_matches_sdk_signature_and_schema_rules():
    kw = pg.ProfileGenerator("k", "claude-opus-5-5", client=object()).request_kwargs({"x": 1})
    inspect.signature(Messages.create).bind(None, **kw)
    for forbidden in ("temperature", "top_p", "top_k", "tool_choice", "thinking"):
        assert forbidden not in kw
    schema = json.dumps(pg.SCHEMA)
    assert kw["output_config"]["format"]["type"] == "json_schema"
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


def test_pdf_layout():
    res = pg.generate_profile(STATE, TURNS, RULES, pg.ProfileGenerator("k", "m", client=FakeClient([GOOD])))
    pdf = pg.render_pdf(res["profile"])
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    text = "\n".join(p.get_text() for p in doc)
    for expected in ("Behavioural resume", "Roles covered", "Behaviour shown", "Team lead, Talk'n'Job",
                     "since 2024", "Power BI, Scrum", "Standing by standards", "Enterprising & Performing",
                     "Situation:", "Result:", "Candidate P01"):
        assert expected in text, expected
    for hidden in ("integrity", "fallback", "Anna", "Looking back", "Lead the team"):
        assert hidden.lower() not in text.lower(), hidden
    assert doc.page_count == 1
