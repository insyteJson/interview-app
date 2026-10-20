"""Deterministic interview engine.

All interview logic lives here: which question comes next, counters, declines,
stopping rules. The language model never writes a question. It only classifies
the participant's latest answer (see classifier.py); this module turns that
classification into the next fixed question.

The rules follow Appendix C (Micro-Interview Protocol) plus the iteration-2
changes listed in the README.
"""

from __future__ import annotations

import copy
import re

PROMPT_VERSION = "app-v3"
CEILING_JOB = 3
DIMENSIONS = ["ability", "integrity", "benevolence"]

BRIEFING = (
    "Welcome, and thank you for taking part.\n\n"
    "Before we start:\n"
    "- There are no right or wrong answers, and nothing you say is scored or judged.\n"
    "- Short answers and long answers are both fine. Any moment you remember is fine, big or small.\n"
    "- The questions are the same for every participant, so some may sound repetitive.\n"
    "- Please describe what actually happened rather than what usually happens.\n"
    "- You can type or dictate your answers. You can write \"skip\" at any time and we will move on.\n"
    "- Take your time.\n\n"
    "Let's begin."
)

Q = {
    "Q_ROLES": "First, please add the roles you would like to talk about: your jobs, and if you like, volunteering "
               "or hobbies. A few words per entry are enough.",
    "Q_TOOLS_JOB": "In that role, which tools, methods or areas of knowledge did you mainly work with?",
    "Q_TOOLS_ACT": "In that activity, which tools, methods or areas of knowledge do you mainly use?",
    "Q_AIM": "Let's look at your role as {label}. What would you say was the primary purpose of that role?",
    "P_ABILITY_1": "In your role as {label}, think of a specific moment where your skill or judgment directly "
                   "determined whether something succeeded or failed.",
    "P_ABILITY_2": "In your role as {label}, think of any moment where what you specifically knew or could do "
                   "mattered to how something turned out.",
    "P_INTEGRITY_1": "In your role as {label}, think of a specific moment where doing the right thing would have "
                     "cost you something — time, credit, or convenience.",
    "P_INTEGRITY_2": "In your role as {label}, think of any moment where you stuck to a standard or principle "
                     "even though nobody would have required it of you.",
    "P_BENEVOLENCE_1": "In your role as {label}, think of a moment where you helped someone at a real cost to "
                       "yourself — time, effort, or standing.",
    "P_BENEVOLENCE_2": "In your role as {label}, think of any moment where you went out of your way to do "
                       "something good for someone else, without it benefiting you directly.",
    "Q_SIT": "What were the general circumstances leading up to this?",
    "Q_ACT": "What did you do, specifically?",
    "Q_OUT": "What happened as a result? Please describe what actually happened or changed.",
    "Q_DIFF": "Is there a different moment you remember more clearly?",
    "Q_TRAIT": "Can you walk me through one specific instance of that, rather than in general?",
    "Q_REFL": "Looking back, is there anything you would do differently?",
    "Q_CONT": "Do you have another example like that from your role as {label}, or shall we move on?",
    "Q_STOP": "Would you like to stop the interview here?",
}

# One fixed clarifying clause per question (Appendix C §8: "repeat the same
# question with one clarifying clause added"). [design choice, iteration 2]
_OCCASION = "(I'm asking about one particular occasion that you remember.)"
CLARIFY = {
    "Q_AIM": "(In a sentence or two: what was the role mainly meant to achieve?)",
    "Q_TOOLS_JOB": "(For example software, machines, methods or subject areas. A few keywords are enough.)",
    "Q_TOOLS_ACT": "(For example equipment, techniques or subject areas. A few keywords are enough.)",
    "Q_SIT": "(What was the situation at the time?)",
    "Q_ACT": "(What did you yourself do in that moment?)",
    "Q_OUT": "(What was different afterwards?)",
    "Q_DIFF": _OCCASION,
    "Q_TRAIT": _OCCASION,
    "Q_REFL": "(If the same moment happened again.)",
    "Q_CONT": "(Another moment from the same role, or we can go on.)",
}
CLAR_SAME = "(The questions follow the same fixed sequence for every moment and every item on your list.)"

T = {
    "T_FIRSTPOS": "Now, the first item on your list.",
    "T_NEXTPOS": "Now, the next item on your list.",
    "T_MOMENT": "Next, I'd like to hear about a specific moment.",
    "T_COMP": "I'll now ask a few short questions about that moment, one at a time.",
    "T_REFL": "One last question about this moment.",
}

CLOSE_COMPLETE = "That completes the interview. Thank you for your time."
CLOSE_ABORT = "Thank you. The interview ends here."

LABELS = {"ANSWER", "DECLINE", "TRAIT", "VAGUE", "MISUNDERSTOOD", "COMPLAINT", "STOP"}

# Words ignored when checking that a restatement uses the participant's own words.
_STOP = set(
    "a an the and or but so of to in on at for with by from as is was were be been being am are it its this that "
    "these those there here then than you your yours i me my mine we us our ours he she they them their his her "
    "did do does done had has have having not no yes just very really also about into out up down over after "
    "before when what which who whom how why all any some more most much many said say".split()
)


def _clarifier(qid: str | None) -> str:
    if qid and qid.startswith("P_"):
        return _OCCASION
    return CLARIFY.get(qid or "", _OCCASION)


def _prompt_qid(dimension: str, level: str) -> str:
    return f"P_{dimension.upper()}_{1 if level == 'primary' else 2}"


def new_state(session_id: str, model: str, max_positions: int = 0) -> dict:
    return {
        "session_id": session_id,
        "prompt_version": PROMPT_VERSION,
        "model": model,
        "status": "listing",
        "turn": 0,
        "positions": [],
        "max_positions": max_positions,
        "pos_idx": 0,
        "dimension": None,
        "level": None,
        "phase": "form",
        "prev_phase": None,
        "counts": {d: 0 for d in DIMENSIONS},
        "prompt_flags": {"trait_redirected": False, "diff_asked": False},
        "incident": None,
        "incidents": [],
        "cur_incident": None,
        "repeat_complaints": 0,
        "last_qid": None,
        "last_qtext": None,
        "last_question": None,
    }


def start(state: dict) -> tuple[dict, dict]:
    """First turn: briefing plus the request to fill in the role form (roles.py)."""
    s = copy.deepcopy(state)
    out = _ask(s, "Q_ROLES", Q["Q_ROLES"])
    return s, {"output": BRIEFING + "\n\n" + out, "rule": "start", "qid": "Q_ROLES",
               "accepted": False, "declined": False}


def set_positions(state: dict, positions: list[dict]) -> tuple[dict, dict]:
    """Apply the submitted role form (already validated by roles.normalise). Jobs first, then the rest, each in
    the order entered; then the first position's aim question. No model is involved in this step."""
    if state["phase"] != "form":
        raise ValueError("the role form was already submitted")
    usable = [p for p in positions if (p.get("title") or "").strip() and p.get("type") in ("job", "non-work")]
    if not usable:
        raise ValueError("no usable role in the form")
    s = copy.deepcopy(state)
    res = {"output": "", "rule": "positions-form", "qid": None, "accepted": False, "declined": False}
    s["turn"] += 1
    ordered = [p for p in usable if p["type"] == "job"] + [p for p in usable if p["type"] != "job"]
    ordered = [dict(p, aim=None, tools=[]) for p in ordered]
    if s.get("max_positions"):
        s["positions_dropped"] = ordered[s["max_positions"]:]
        ordered = ordered[: s["max_positions"]]
    s["positions"], s["status"], s["pos_idx"] = ordered, "interview", 0
    res["output"] = _start_position(s, "T_FIRSTPOS")
    res["qid"] = s["last_qid"]
    return s, res


# ---------------------------------------------------------------- helpers

def _ask(s: dict, qid: str, qtext: str, transition: str | None = None, restatement: str | None = None) -> str:
    """Record and render a fixed question (optionally with transition and restatement)."""
    question = f"You said {restatement}. {qtext}" if restatement else qtext
    s["last_qid"], s["last_qtext"], s["last_question"] = qid, qtext, question
    return (T[transition] + "\n\n" if transition else "") + question


def _repeat(s: dict, clause: str) -> str:
    """Repeat the last fixed question with a clarifying clause; state pointers unchanged."""
    return f"{s['last_qtext']} {clause}"


def _cur_label(s: dict) -> str:
    return s["positions"][s["pos_idx"]]["label"]


def _cur_type(s: dict) -> str:
    return s["positions"][s["pos_idx"]]["type"]


def valid_restatement(restatement: str | None, answer: str) -> str | None:
    """Accept a restatement only if it is short and built from the participant's own words."""
    if not restatement:
        return None
    r = restatement.strip().strip("\"'“”").rstrip(".").strip()
    words = re.findall(r"[\w'’-]+", r.lower())
    if not words or len(words) > 25:
        return None
    content = [w for w in words if w not in _STOP]
    if not content:
        return None
    answer_words = set(re.findall(r"[\w'’-]+", answer.lower()))
    overlap = sum(1 for w in content if w in answer_words) / len(content)
    if overlap < 0.6:
        return None
    if r and r[0].isupper() and not r.startswith("I "):
        r = r[0].lower() + r[1:]
    return r


def _merge(inc: dict, cls: dict) -> bool:
    """Merge explicitly stated components into the incident. Returns True if anything new was added."""
    added = False
    for key, field in (("s", "situation"), ("a", "action"), ("o", "outcome")):
        val = (cls.get(field) or "").strip()
        if not val:
            continue
        inc[key] = val if not inc.get(key) else f"{inc[key]} … {val}"
        added = True
        if key == "o":
            inc["o_definite"] = bool(cls.get("outcome_definite")) or bool(inc.get("o_definite"))
    return added


def _new_incident(source_level: str, inherit: dict | None = None) -> dict:
    inherit = inherit or {}
    return {"source_level": source_level, "s": None, "a": None, "o": None, "o_definite": False,
            "outcome_rechecked": False,
            "trait_redirected": inherit.get("trait_redirected", False),
            "diff_asked": inherit.get("diff_asked", False), "diff_pending": False}


# ---------------------------------------------------------------- transitions

def _start_position(s: dict, transition: str) -> str:
    s["dimension"], s["level"], s["phase"] = "aim", None, "aim"
    s["counts"] = {d: 0 for d in DIMENSIONS}
    s["incident"] = None
    return _ask(s, "Q_AIM", Q["Q_AIM"].format(label=_cur_label(s)), transition)


def _start_dimension(s: dict, dimension: str, level: str = "primary", transition: str | None = "T_MOMENT") -> str:
    s["dimension"], s["level"], s["phase"] = dimension, level, "prompt"
    s["prompt_flags"] = {"trait_redirected": False, "diff_asked": False}
    s["incident"] = None
    qid = _prompt_qid(dimension, level)
    return _ask(s, qid, Q[qid].format(label=_cur_label(s)), transition)


def _next_dimension(s: dict, res: dict) -> str:
    i = DIMENSIONS.index(s["dimension"])
    if i + 1 < len(DIMENSIONS):
        res["rule"] = "next-dimension"
        return _start_dimension(s, DIMENSIONS[i + 1])
    s["pos_idx"] += 1
    if s["pos_idx"] < len(s["positions"]):
        res["rule"] = "next-position"
        return _start_position(s, "T_NEXTPOS")
    return _complete(s, res)


def _complete(s: dict, res: dict) -> str:
    s["status"], s["phase"] = "complete", "done"
    s["last_qid"] = s["last_qtext"] = s["last_question"] = None
    res["rule"] = "complete"
    return CLOSE_COMPLETE


def _decline_rule(s: dict, res: dict) -> str:
    res["declined"] = True
    s["incident"] = None
    if s["level"] == "primary" and _cur_type(s) == "job":
        res["rule"] = "decline-fallback"
        return _start_dimension(s, s["dimension"], "fallback", transition=None)
    res["rule"] = "decline-next"
    return _next_dimension(s, res)


def _accept(s: dict, res: dict) -> str:
    inc = s["incident"]
    s["counts"][s["dimension"]] += 1
    s["incidents"].append({
        "id": f"i{len(s['incidents']) + 1:02d}",
        "position_label": _cur_label(s),
        "position_type": _cur_type(s),
        "dimension": s["dimension"],
        "level": inc["source_level"],
        "situation": inc["s"], "action": inc["a"], "outcome": inc["o"],
        "reflection": None,
        "turn_accepted": s["turn"],
    })
    s["cur_incident"] = len(s["incidents"]) - 1
    s["incident"] = None
    s["phase"] = "reflection"
    res["accepted"], res["rule"] = True, "accept"
    return _ask(s, "Q_REFL", Q["Q_REFL"], "T_REFL")


def _component_step(s: dict, cls: dict, answer: str, res: dict, transition: str | None, usable: bool) -> str:
    inc = s["incident"]
    if inc["s"] and inc["a"] and inc["o"]:
        if inc["o_definite"]:
            return _accept(s, res)
        if not inc["outcome_rechecked"]:
            inc["outcome_rechecked"] = True
            res["rule"] = "outcome-recheck"
            rest = valid_restatement(cls.get("restatement"), answer)
            return _ask(s, "Q_OUT", Q["Q_OUT"], transition, rest)
        return _vague_in_components(s, res)
    for key, qid in (("s", "Q_SIT"), ("a", "Q_ACT"), ("o", "Q_OUT")):
        if not inc[key]:
            break
    rest = valid_restatement(cls.get("restatement"), answer) if usable else None
    if rest:
        res["rule"] = "restate-partial"
        return _ask(s, qid, Q[qid], transition, rest)
    if s["last_qid"] == qid and not usable:
        res["rule"] = "repeat-clarify"
        return _ask(s, qid, Q[qid], transition) + " " + _clarifier(qid)
    res["rule"] = "component"
    return _ask(s, qid, Q[qid], transition)


def _vague_in_components(s: dict, res: dict) -> str:
    inc = s["incident"]
    if not inc["diff_asked"]:
        inc["diff_asked"] = inc["diff_pending"] = True
        res["rule"] = "vague-different"
        return _ask(s, "Q_DIFF", Q["Q_DIFF"])
    return _decline_rule(s, res)


# ---------------------------------------------------------------- main step

def step(state: dict, answer: str, cls: dict) -> tuple[dict, dict]:
    """Apply one participant answer. `cls` is the classifier output. Returns (new_state, result)."""
    s = copy.deepcopy(state)
    res = {"output": "", "rule": None, "qid": None, "accepted": False, "declined": False}
    label = cls.get("label") if cls.get("label") in LABELS else "VAGUE"

    if s["status"] in ("complete", "aborted"):
        res.update(output=CLOSE_COMPLETE if s["status"] == "complete" else CLOSE_ABORT, rule="closed")
        return s, res

    if s["phase"] == "form":
        # the role form is submitted through set_positions(); a chat answer is not expected here
        res.update(output=s["last_question"], rule="form-pending", qid=s["last_qid"])
        return s, res

    s["turn"] += 1
    phase = s["phase"]
    out = None

    # --- stop confirmation
    if phase == "stop-confirm":
        if cls.get("confirm") == "yes" or label == "STOP":
            s["status"], s["phase"] = "aborted", "done"
            res.update(output=CLOSE_ABORT, rule="aborted")
            return s, res
        s["phase"], s["prev_phase"] = s["prev_phase"], None
        res.update(output=s["last_question"], rule="stop-declined", qid=s["last_qid"])
        return s, res

    if label == "STOP":
        s["prev_phase"], s["phase"] = phase, "stop-confirm"
        s["repeat_complaints"] = 0
        res.update(output=Q["Q_STOP"], rule="stop-ask", qid="Q_STOP")
        return s, res

    if label == "COMPLAINT":
        s["repeat_complaints"] += 1
        if s["repeat_complaints"] >= 2:
            s["prev_phase"], s["phase"] = phase, "stop-confirm"
            s["repeat_complaints"] = 0
            res.update(output=Q["Q_STOP"], rule="complaint-stop-ask", qid="Q_STOP")
            return s, res
        res.update(output=_repeat(s, CLAR_SAME), rule="complaint-repeat", qid=s["last_qid"])
        return s, res
    s["repeat_complaints"] = 0

    if label == "MISUNDERSTOOD":
        res.update(output=_repeat(s, _clarifier(s["last_qid"])), rule="clarify", qid=s["last_qid"])
        return s, res

    # --- phase handlers
    if phase == "aim":
        s["positions"][s["pos_idx"]]["aim"] = answer
        res["rule"] = "aim-recorded"
        s["phase"] = "tools"
        qid = "Q_TOOLS_JOB" if _cur_type(s) == "job" else "Q_TOOLS_ACT"
        out = _ask(s, qid, Q[qid])

    elif phase == "tools":
        tools = [] if label == "DECLINE" else [t.strip() for t in (cls.get("tools") or []) if t and t.strip()]
        s["positions"][s["pos_idx"]]["tools"] = tools[:12]
        res["rule"] = "tools-recorded"
        out = _start_dimension(s, "ability")

    elif phase == "prompt":
        flags = s["prompt_flags"]
        if label == "DECLINE":
            out = _decline_rule(s, res)
        elif label == "TRAIT":
            if not flags["trait_redirected"]:
                flags["trait_redirected"] = True
                res["rule"] = "trait-redirect"
                out = _ask(s, "Q_TRAIT", Q["Q_TRAIT"])
            else:
                out = _decline_rule(s, res)
        elif label == "VAGUE":
            if not flags["diff_asked"]:
                flags["diff_asked"] = True
                res["rule"] = "vague-different"
                out = _ask(s, "Q_DIFF", Q["Q_DIFF"])
            else:
                out = _decline_rule(s, res)
        else:  # ANSWER: a moment was offered
            s["incident"] = _new_incident(s["level"], flags)
            usable = _merge(s["incident"], cls)
            s["phase"] = "components"
            out = _component_step(s, cls, answer, res, "T_COMP", usable or bool(cls.get("restatement")))

    elif phase == "components":
        inc = s["incident"]
        if label == "DECLINE":
            out = _decline_rule(s, res)
        elif label == "TRAIT" and not inc["trait_redirected"]:
            inc["trait_redirected"] = True
            res["rule"] = "trait-redirect"
            out = _ask(s, "Q_TRAIT", Q["Q_TRAIT"])
        elif label in ("VAGUE", "TRAIT"):
            if inc["diff_pending"]:  # no clearer moment after Q_DIFF
                out = _decline_rule(s, res)
            else:
                out = _vague_in_components(s, res)
        else:  # ANSWER
            transition = None
            if inc["diff_pending"]:  # a different, clearer moment: restart Stage 2
                s["incident"] = _new_incident(inc["source_level"], {"diff_asked": True})
                transition = "T_COMP"
            usable = _merge(s["incident"], cls)
            out = _component_step(s, cls, answer, res, transition, usable)

    elif phase == "reflection":
        s["incidents"][s["cur_incident"]]["reflection"] = answer
        if _cur_type(s) == "job" and s["counts"][s["dimension"]] < CEILING_JOB:
            s["phase"] = "continuation"
            res["rule"] = "continuation"
            out = _ask(s, "Q_CONT", Q["Q_CONT"].format(label=_cur_label(s)))
        else:
            out = _next_dimension(s, res)

    elif phase == "continuation":
        if label == "ANSWER":
            s["level"] = "continuation"
            s["incident"] = _new_incident("continuation")
            usable = _merge(s["incident"], cls)
            s["phase"] = "components"
            out = _component_step(s, cls, answer, res, "T_COMP", usable)
        else:
            res["declined"] = True
            out = _next_dimension(s, res)

    else:
        res["rule"] = "unknown-phase"
        out = s.get("last_question") or CLOSE_COMPLETE

    res["output"] = out
    res["qid"] = s["last_qid"]
    return s, res
