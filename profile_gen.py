"""Behavioural resume generation (profile version `resume-v1`, iteration 2).

What the language model writes, one call per accepted incident: a headline, a situation line, a result line,
up to two competency labels (Bartram 2005, Great Eight factor names), circumstance tags, and the tools or methods
named in the incident. Everything else is done by code: the grouping by theme (fixed by the question that elicited
the incident), the role list with company, period and stated tools (from the interview), the counts, the layout.

Checks after every model call (failure -> regenerate, max. 3 attempts, all attempts logged, nothing edited by hand):
word limits, numbers that are not in the answers, a verbatim evidence quote (>= 3 words) for every label and tag,
tools that do not appear in the answers, redacted personal names, personal names reported by the model.

Redaction policy (decided 18.10.2026): personal names of people are removed; company names, job titles, places and
achievements stay, at the level the CV shows them.
"""

from __future__ import annotations

import html
import io
import json
import re
import time
from collections import Counter
from datetime import datetime, timezone

import pymupdf

PROFILE_VERSION = "resume-v1"

DIMENSIONS = ("ability", "integrity", "benevolence")
# Decision 2 (resolved): neutral headings; subtitles paraphrase Mayer, Davis & Schoorman (1995: 717-719).
HEADINGS = {"ability": "Doing the work", "integrity": "Standing by standards", "benevolence": "Helping others"}
SUBTITLES = {
    "ability": "Situations showing what the candidate can do: the skills and judgement they applied, and in which role.",
    "integrity": "Situations where the candidate kept to a standard or principle, including when it cost them something.",
    "benevolence": "Situations where the candidate did something for others without benefiting from it themselves.",
}
TYPE_LABEL = {"job": "Paid", "non-work": "Outside work"}  # sessions before app-v3 (no category)
CATEGORY_LABEL = {"job": "Job", "volunteering": "Volunteering", "hobby": "Hobby"}

# Competency labels: factor names from Bartram (2005); definitions are own operationalisations
# (coding agenda "181026 - Coding Agenda Competency Labels v1").
LABELS = ["Leading & Deciding", "Supporting & Cooperating", "Interacting & Presenting", "Analysing & Interpreting",
          "Creating & Conceptualising", "Organising & Executing", "Adapting & Coping", "Enterprising & Performing"]
TAGS = ["Self-initiated", "Under time pressure", "Involved conflict or disagreement"]

MAX_HEADLINE = 12
MAX_LINE = 35          # situation and result, each
MAX_LABELS = 2
MAX_ATTEMPTS = 3
MAX_TOKENS = 8000

OPENER_QIDS_PREFIX = ("P_",)
OPENER_QIDS = {"Q_TRAIT", "Q_DIFF", "Q_CONT"}

EVALUATIVE = {"successful", "successfully", "excellent", "great", "outstanding", "impressive", "strong", "effective",
              "effectively", "efficient", "efficiently", "valuable", "exceptional", "remarkable", "skilled",
              "dedicated", "admirable", "perfect", "perfectly", "brilliant", "proactive", "proactively"}

SYSTEM = """You write one entry of a behavioural resume from a Critical Incident interview.

INPUT: a JSON object with the role and the exchange that describes ONE incident: the interviewer's questions and
the candidate's answers, in order. The answers may contain dictation errors and fillers.

OUTPUT FIELDS
- headline: at most 12 words. Starts with a past-tense verb and restates the action the candidate took. Never a
  trait, never an evaluation. Example: "Brought deployment pipeline and documentation in-house from a contractor".
- situation: one or two short sentences, at most 35 words: the circumstances that led to the action.
- result: one or two short sentences, at most 35 words: what happened or changed afterwards. If the candidate only
  claimed a counterfactual ("we would have been lost"), write it as the candidate's claim ("According to the
  candidate, ..."). Never state a cause-effect link the candidate did not state as a fact.
- labels: zero to two competency labels from the closed list below, for the candidate's CENTRAL action only.
- label_evidence: for every label, one exact quote of at least three consecutive words copied verbatim from the
  candidate's answers that states the labelled action.
- tags: circumstance tags from the closed list below, only if the answers state the feature explicitly.
- tag_evidence: for every tag, one exact verbatim quote of at least three consecutive words from the answers.
- tools: tools, software, equipment or methods the candidate names in THESE answers, as short terms spelled as in
  the answers (fix only obvious dictation errors in capitalisation). Empty list if none.
- personal_names: every personal name of a person (first or last name) that still appears in the answers. Company,
  club, product and place names are NOT personal names. Empty list if none. Never use a personal name in your text;
  refer to people by their role ("the CEO", "a colleague", "a friend").

WRITING RULES
- Use only content stated in the candidate's answers about this incident. Never infer, embellish or add context.
- Write about "the candidate" in the third person. Plain, neutral English. Fix grammar and dictation errors; keep
  company names, job titles, places, tools and achievements as stated.
- No evaluative adjectives about the candidate or the outcome, no traits, no scores, no recommendations. If the
  candidate called something a success, you may write that the candidate described it as a success.
- Ignore general statements about habits that are not part of this incident.

COMPETENCY LABELS (closed list; general rules: label the central action only; explicit text only; at most two;
no inference from the role or title; the section the incident sits in is irrelevant)
1. "Leading & Deciding": the candidate made a decision, set a direction, took charge of something, or directed
   other people's work.
2. "Supporting & Cooperating": the candidate acted to support, protect or help a specific person or group, or worked
   jointly with others toward a shared aim.
3. "Interacting & Presenting": the candidate explained, persuaded, negotiated, mediated, presented or ran a
   conversation or meeting.
4. "Analysing & Interpreting": the candidate analysed information, diagnosed a problem, or applied technical or
   specialist knowledge.
5. "Creating & Conceptualising": the candidate designed, invented or proposed something new, or deliberately
   learned a new skill or subject for the task.
6. "Organising & Executing": the candidate planned, prepared, organised, or carried work through to completion or to
   a quality standard.
7. "Adapting & Coping": the candidate changed their approach because circumstances changed, or kept acting
   effectively despite stated pressure or a setback.
8. "Enterprising & Performing": the candidate set themselves a goal beyond the requirement, or invested extra effort
   to reach a higher result.
Boundary rules: helping a person = Supporting; label Interacting only if the communication itself is the action.
Finishing to the expected standard = Organising; going beyond it = Enterprising. Deciding what happens = Leading;
arranging how it happens = Organising.

CIRCUMSTANCE TAGS (closed list; apply only if explicitly stated; if in doubt, do not apply)
- "Self-initiated": the answers explicitly state that the action was unprompted or on the candidate's own initiative.
- "Under time pressure": a deadline, rush period or time limit is explicitly mentioned for this incident.
- "Involved conflict or disagreement": the answers state another party's opposing request, instruction or position.
"""

_EVIDENCE = {"type": "array", "items": {"type": "object",
                                        "properties": {"name": {"type": "string"}, "quote": {"type": "string"}},
                                        "required": ["name", "quote"], "additionalProperties": False}}
SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "situation": {"type": "string"},
        "result": {"type": "string"},
        "labels": {"type": "array", "items": {"type": "string", "enum": LABELS}},
        "label_evidence": _EVIDENCE,
        "tags": {"type": "array", "items": {"type": "string", "enum": TAGS}},
        "tag_evidence": _EVIDENCE,
        "tools": {"type": "array", "items": {"type": "string"}},
        "personal_names": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "situation", "result", "labels", "label_evidence", "tags", "tag_evidence", "tools",
                 "personal_names"],
    "additionalProperties": False,
}


class GenerationError(RuntimeError):
    pass


# ------------------------------------------------------------------ redaction (personal names only)

def parse_rules(text: str) -> list[tuple[str, str]]:
    """One rule per line: `find => replacement`. A line without `=>` is replaced by "[name removed]"."""
    rules = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=>" in line:
            find, repl = (p.strip() for p in line.split("=>", 1))
        else:
            find, repl = line, "[name removed]"
        if len(find) >= 2:
            rules.append((find, repl))
    return sorted(rules, key=lambda r: len(r[0]), reverse=True)


def apply_rules(text: str | None, rules: list[tuple[str, str]]) -> str | None:
    if text is None:
        return None
    for find, repl in rules:
        text = re.sub(r"(?<!\w)" + re.escape(find) + r"(?!\w)", repl, text, flags=re.I)
    return text


_STOP = {"I", "I'm", "I'd", "I've", "I'll", "CEO", "CFO", "CTO", "IT", "UI", "UX", "AM", "PM", "OK", "Yeah", "So",
         "Well", "No", "Yes", "And", "But", "The", "Again", "Here", "Looking", "Is", "Not", "In", "Basically", "A", "An",
         "My", "We", "It", "That", "Then", "There", "This", "Of", "Or", "Like", "Now", "Also", "Just", "Even",
         "For", "To", "At", "On", "As", "If", "When", "What", "Because", "Maybe", "Um", "Uh", "Oh", "Let's", "Do",
         "Did", "Number", "Course", "All", "Nothing", "HR", "AI", "API", "Scrum", "SCRUM"}


def suggest_identifiers(texts: list[str]) -> list[tuple[str, int]]:
    """Capitalised word sequences that may be personal names. A reading aid, not a detector."""
    found: Counter = Counter()
    for t in texts:
        for m in re.finditer(r"\b[A-Z][\w'’&.-]*(?:\s+[A-Z][\w'’&.-]*)*", t or ""):
            words = [w for w in m.group(0).split() if w not in _STOP]
            if words:
                found[" ".join(words)] += 1
    return found.most_common()


# ------------------------------------------------------------------ incident source text

def incident_exchanges(incident: dict, turns: list[dict]) -> list[dict]:
    """Questions and answers from the answer that opened the moment up to the acceptance turn."""
    by_turn = {t["turn"]: t for t in turns}
    end = incident["turn_accepted"]
    start, t = end, end
    while t > 0:
        prev, row = by_turn.get(t - 1), by_turn.get(t)
        label = ((row or {}).get("classification") or {}).get("label")
        asked = (prev or {}).get("qid") or ""
        if label == "ANSWER" and (asked.startswith(OPENER_QIDS_PREFIX) or asked in OPENER_QIDS):
            start = t
            break
        t -= 1
    out = []
    for n in range(start, end + 1):
        row, prev = by_turn.get(n), by_turn.get(n - 1)
        if row and prev:
            out.append({"question": prev["question_shown"].split("\n\n")[-1], "answer": row["answer_raw"]})
    return out


# ------------------------------------------------------------------ checks

def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split())


def _evidence_ok(name: str, evidence: list[dict], src_n: str) -> bool:
    return any(e["name"].lower() == name.lower() and len(_norm(e["quote"]).split()) >= 3 and _norm(e["quote"]) in src_n
               for e in evidence)


def check_entry(entry: dict, source: str, rules: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    """Returns (hard failures, warnings)."""
    fails, warns = [], []
    head, sit, res = (entry.get(k, "").strip() for k in ("headline", "situation", "result"))
    for name, text, limit in (("headline", head, MAX_HEADLINE), ("situation", sit, MAX_LINE), ("result", res, MAX_LINE)):
        n = len(text.split())
        if n == 0 or n > limit:
            fails.append(f"{name} has {n} words (allowed 1-{limit})")
    written = " ".join((head, sit, res))
    src_n = _norm(source)
    src_nums = set(re.findall(r"\d+", source))
    for num in sorted(set(re.findall(r"\d+", written)) - src_nums):
        fails.append(f"number '{num}' does not appear in the answers")
    if len(entry.get("labels", [])) > MAX_LABELS:
        fails.append(f"{len(entry['labels'])} labels (allowed at most {MAX_LABELS})")
    for lab in entry.get("labels", []):
        if not _evidence_ok(lab, entry.get("label_evidence", []), src_n):
            fails.append(f"label '{lab}' has no verbatim evidence quote from the answers")
    for tag in entry.get("tags", []):
        if not _evidence_ok(tag, entry.get("tag_evidence", []), src_n):
            fails.append(f"tag '{tag}' has no verbatim evidence quote from the answers")
    for tool in entry.get("tools", []):
        if _norm(tool) and _norm(tool) not in src_n:
            fails.append(f"tool '{tool}' does not appear in the answers")
    for find, _ in rules:
        if re.search(r"(?<!\w)" + re.escape(find) + r"(?!\w)", written, flags=re.I):
            fails.append(f"redacted name '{find}' appears in the text")
    if entry.get("personal_names"):
        fails.append("personal names still in the answers (add a redaction rule): " + ", ".join(entry["personal_names"]))
    for w in sorted((set(_norm(written).split()) & EVALUATIVE) - set(src_n.split())):
        warns.append(f"evaluative word '{w}' is not in the answers")
    return fails, warns


def _normalise(entry: dict) -> dict:
    lab = {x.lower(): x for x in LABELS}
    tag = {x.lower(): x for x in TAGS}
    entry["labels"] = [lab[x.lower()] for x in entry.get("labels", []) if x.lower() in lab]
    entry["tags"] = sorted({tag[x.lower()] for x in entry.get("tags", []) if x.lower() in tag}, key=TAGS.index)
    seen, tools = set(), []
    for t in entry.get("tools", []):
        t = t.strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            tools.append(t)
    entry["tools"] = tools
    return entry


# ------------------------------------------------------------------ generation

class ProfileGenerator:
    def __init__(self, api_key: str, model: str, effort: str = "medium", client=None):
        if client is None:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=180)
        self.client, self.model, self.effort = client, model, effort

    def request_kwargs(self, payload: dict, feedback: str | None = None) -> dict:
        content = json.dumps(payload, ensure_ascii=False)
        if feedback:
            content += "\n\nYour previous attempt failed these checks; write a new entry that passes them:\n" + feedback
        return dict(
            model=self.model, max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": content}],
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
        )

    def call(self, payload: dict, feedback: str | None = None) -> tuple[dict, dict]:
        t0 = time.monotonic()
        msg = self.client.messages.create(**self.request_kwargs(payload, feedback))
        meta = {"model": getattr(msg, "model", self.model), "effort": self.effort,
                "stop_reason": getattr(msg, "stop_reason", None),
                "input_tokens": getattr(msg.usage, "input_tokens", 0),
                "output_tokens": getattr(msg.usage, "output_tokens", 0),
                "latency_s": round(time.monotonic() - t0, 1)}
        if meta["stop_reason"] in ("refusal", "max_tokens"):
            raise GenerationError(f"stop_reason={meta['stop_reason']}")
        text = next((b.text for b in msg.content if getattr(b, "type", "") == "text"), None)
        if not text:
            raise GenerationError("no text block in response")
        return _normalise(json.loads(text)), meta


def role_display(p: dict) -> str:
    title = p.get("title") or p.get("label") or ""
    kind = p.get("employment")
    if kind and kind.split(" /")[0].lower() not in title.lower():
        title = f"{title} ({kind})"
    company = p.get("company")
    return f"{title}, {company}" if company and company.lower() not in title.lower() else title


def generate_profile(state: dict, turns: list[dict], rules: list[tuple[str, str]], generator: ProfileGenerator,
                     progress=None) -> dict:
    """Builds the full resume. Returns a dict with `profile`, `log`, `ok`."""
    positions = state.get("positions") or []
    incidents = state.get("incidents") or []
    pos_by_label = {p["label"]: p for p in positions}
    log = {"profile_version": PROFILE_VERSION, "model": generator.model, "effort": generator.effort,
           "started": datetime.now(timezone.utc).isoformat(), "redaction_rules": len(rules), "incidents": []}
    themes = {d: [] for d in DIMENSIONS}
    for k, inc in enumerate(incidents):
        if progress:
            progress(k, len(incidents))
        pos = pos_by_label.get(inc["position_label"], {"title": inc["position_label"], "type": inc.get("position_type")})
        exch = [{"question": apply_rules(e["question"], rules), "answer": apply_rules(e["answer"], rules)}
                for e in incident_exchanges(inc, turns)]
        payload = {"role": apply_rules(role_display(pos), rules),
                   "role_type": "paid position" if pos.get("type") == "job" else "activity outside work",
                   "exchange": exch}
        source = " ".join(e["answer"] for e in exch)
        attempts, feedback, accepted = [], None, None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                entry, meta = generator.call(payload, feedback)
            except (GenerationError, json.JSONDecodeError, KeyError) as exc:
                attempts.append({"attempt": attempt, "error": str(exc)})
                feedback = None
                continue
            fails, warns = check_entry(entry, source, rules)
            attempts.append({"attempt": attempt, "meta": meta, "fails": fails, "warnings": warns, "entry": entry})
            if not fails:
                accepted = entry | {"warnings": warns}
                break
            feedback = "\n".join("- " + f for f in fails)
        log["incidents"].append({"id": inc["id"], "turn_accepted": inc["turn_accepted"], "attempts": attempts,
                                 "passed": accepted is not None})
        if accepted:
            themes[inc["dimension"]].append({
                "id": inc["id"], "role": apply_rules(role_display(pos), rules), "role_type": pos.get("type"),
                "headline": accepted["headline"].strip(), "situation": accepted["situation"].strip(),
                "result": accepted["result"].strip(), "labels": accepted["labels"],
                "label_evidence": accepted.get("label_evidence", []), "tags": accepted["tags"],
                "tag_evidence": accepted.get("tag_evidence", []), "tools": accepted["tools"],
                "elicited_by": inc.get("level"), "warnings": accepted["warnings"], "source": exch,
            })
    roles = [{"title": apply_rules(p.get("title") or p["label"], rules), "company": p.get("company"),
              "period": p.get("period"), "type": p.get("type"), "category": p.get("category"), "display": apply_rules(role_display(p), rules),
              "tools_stated": [apply_rules(t, rules) for t in p.get("tools") or []]} for p in positions]
    profile = {"candidate_id": state["session_id"], "profile_version": PROFILE_VERSION,
               "generated_at": datetime.now(timezone.utc).isoformat(), "model": generator.model,
               "roles": roles, "themes": themes, "label_counts": label_counts(themes)}
    log["finished"] = datetime.now(timezone.utc).isoformat()
    return {"profile": profile, "log": log, "ok": all(i["passed"] for i in log["incidents"])}


def label_counts(themes: dict) -> list[tuple[str, int]]:
    c = Counter(lab for d in DIMENSIONS for e in themes[d] for lab in e["labels"])
    return sorted(c.items(), key=lambda x: (-x[1], LABELS.index(x[0])))


# ------------------------------------------------------------------ rendering

CSS = """
* { font-family: sans-serif; }
body { font-size: 9pt; color: #1b1f24; line-height: 1.25; }
h1 { font-size: 20pt; margin: 0; }
.lede { font-size: 8.8pt; color: #57606a; margin: 2pt 0 7pt 0; }
table.two { width: 100%; border-collapse: collapse; border-top: 2px solid #1f5fa8; margin: 0; }
table.two td { vertical-align: top; padding: 5pt 0 0 0; }
td.c1 { width: 60%; padding-right: 12pt; }
td.c2 { width: 40%; }
.colh { font-size: 8.5pt; text-transform: uppercase; letter-spacing: 1pt; color: #57606a; font-weight: bold; margin: 0 0 3pt 0; }
.li { margin: 0 0 1.5pt 0; font-size: 9pt; }
.li b { color: #1f5fa8; }
.tl { margin: -1pt 0 2pt 0; font-size: 7.8pt; color: #6e7781; }
.tagline { font-size: 7.6pt; color: #57606a; margin: 4pt 0 2pt 0; padding-top: 3pt; border-top: 1px solid #c9d3df; }
h3 { font-size: 11.5pt; color: #1f5fa8; margin: 7pt 0 0 0; border-bottom: 1px solid #c9d3df; padding-bottom: 2pt; }
.th-sub { font-size: 8.3pt; color: #57606a; margin: 2pt 0 3pt 0; }
.hl { font-weight: bold; font-size: 9.5pt; margin: 4pt 0 0 0; }
.meta { font-size: 8.2pt; color: #57606a; margin: 0 0 1pt 0; }
.lbl { font-size: 8.2pt; color: #1f5fa8; font-weight: bold; }
.tag { color: #9a5b00; font-size: 8.2pt; font-weight: bold; }
.tool { color: #6e7781; font-size: 8.2pt; }
.ln { margin: 0; }
.lab { color: #57606a; }
.none { font-size: 8.5pt; color: #6e7781; margin: 2pt 0 0 0; }
"""


def _e(s) -> str:
    return html.escape(s or "")


def _keep(s: str) -> str:
    """Keep a short label on one line."""
    return _e(s).replace("-", "‑").replace(" ", " ")


def render_html(profile: dict) -> str:
    roles_html = ""
    for r in profile["roles"]:
        kind = CATEGORY_LABEL.get(r.get("category") or "", TYPE_LABEL.get(r["type"], ""))
        roles_html += (f"<p class='li'>{_e(r['display'])} <span class='lab'>({_e(kind)})"
                       "</span></p>")
        sub = [x for x in ([r.get("period")] + [", ".join(r.get("tools_stated") or [])]) if x]
        if sub:
            roles_html += f"<p class='tl'>{_e(' · '.join(sub))}</p>"
    beh = "".join(f"<p class='li'><b>{_e(l)}</b> <span class='lab'>{c}</span></p>" for l, c in profile["label_counts"])
    beh = beh or "<p class='none'>No labelled behaviour.</p>"
    h = [f"<h1>Behavioural resume</h1><p class='lede'>Candidate {_e(profile['candidate_id'])}</p>",
         "<table class='two'><tr>"
         f"<td class='c1'><p class='colh'>Roles covered</p>{roles_html}</td>"
         f"<td class='c2'><p class='colh'>Behaviour shown <span class='lab'>(situations)</span></p>{beh}</td>"
         "</tr></table>",
         "<p class='tagline'>Restated from a structured interview, no ratings. Blue: behaviour shown · "
         "Amber: circumstances mentioned · Grey: period, tools and methods named.</p>"]
    for d in DIMENSIONS:
        entries = profile["themes"][d]
        if not entries:
            continue
        h.append(f"<h3>{_e(HEADINGS[d])}</h3><p class='th-sub'>{_e(SUBTITLES[d])}</p>")
        for x in entries:
            meta = _e(x["role"])
            if x["labels"]:
                meta += " &nbsp;&nbsp;<span class='lbl'>" + " · ".join(_e(l) for l in x["labels"]) + "</span>"
            if x["tags"]:
                meta += " &nbsp;·&nbsp; <span class='tag'>" + " · ".join(_keep(t) for t in x["tags"]) + "</span>"
            if x["tools"]:
                meta += " &nbsp;·&nbsp; <span class='tool'>" + ", ".join(_e(t) for t in x["tools"]) + "</span>"
            h.append(f"<p class='hl'>{_e(x['headline'])}</p><p class='meta'>{meta}</p>"
                     f"<p class='ln'><span class='lab'>Situation:</span> {_e(x['situation'])}</p>"
                     f"<p class='ln'><span class='lab'>Result:</span> {_e(x['result'])}</p>")
    return "<body>" + "".join(h) + "</body>"


def render_pdf(profile: dict) -> bytes:
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    story = pymupdf.Story(html=render_html(profile), user_css=CSS)
    page = pymupdf.paper_rect("a4")
    where = page + (46, 36, -46, -40)
    more = 1
    while more:
        dev = writer.begin_page(page)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
    doc = pymupdf.open(stream=buf.getvalue(), filetype="pdf")
    n = doc.page_count
    for k, pg in enumerate(doc, 1):
        pg.insert_text((46, page.height - 24), f"Candidate {profile['candidate_id']} · page {k} of {n}",
                       fontsize=7.5, color=(0.45, 0.45, 0.45))
    doc.set_metadata({"title": f"Behavioural resume {profile['candidate_id']}"})
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out
