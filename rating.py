"""Candidate questionnaire (DO2), opened through a separate link: https://<your-app>/?rate=P01

Shown only when the researcher shares the link and the session has an approved profile and a CV. The interview
pipeline (engine, classifier, profile generation) is not touched: this page only reads the two stored PDFs and
writes to its own table.

Flow (6 screens). Order is counterbalanced by participant code: odd number -> interview resume first, even -> CV
first. The profile-only screen always directly follows the profile's two screens.

    resume first:  resume 1-4 | resume 5-8 | resume 9-11 | CV 1-4 | CV 5-8 | final 12-15
    CV first:      CV 1-4 | CV 5-8 | resume 1-4 | resume 5-8 | resume 9-11 | final 12-15

Screens without an open question move on by themselves once every item is answered; screens with an open question
end with a visible "Skip and continue" / "Continue" button. Answers are saved per screen and locked on submit.
"""

from __future__ import annotations

import math
import re
import time

import pymupdf
import streamlit as st

INSTRUMENT = "do2-candidate-v1"
SECONDS_PER_SCREEN = 40   # for the "about N min left" estimate only

DOC_LABEL = {"profile": "Your interview resume", "cv": "Your CV"}

SCALE = [1, 2, 3, 4, 5, 6, 7]
NA = "na"
SCALE_NOTE = "1 = strongly disagree · 7 = strongly agree"
NA_NOTE = "“Not covered” = this document says nothing about it"

# ------------------------------------------------------------------ items (the only place wording lives)
# kind: "scale" (1-7), "scale_na" (1-7 or not covered), "choice", "text" (optional)

PRIMARY = [
    ("a1", "scale", "This document represents me accurately."),
    ("a2", "scale", "Everything in this document is true of me."),
    ("a3", "scale", "This document includes what is important about my professional capabilities."),
    ("a4", "scale", "A reader of this document would form a correct impression of my professional capabilities."),
]
DIAGNOSTIC = [
    ("d5", "scale_na", "This document gives a correct picture of how well I do my work."),
    ("d6", "scale_na", "This document gives a correct picture of how I act when standards or commitments are at stake."),
    ("d7", "scale_na", "This document gives a correct picture of how I act when others need help."),
    ("d8", "scale_na", "This document describes how I typically work, not just my best moments."),
]
PROFILE_ONLY = [
    ("p9", "scale", "Everything in this resume comes from what I said in the interview."),
    ("p10", "scale", "The summary of recurring behaviours fits how I typically work."),
    ("p11", "text", "Which entries, if any, would you remove or correct, and why? (optional)"),
]
FINAL = [
    ("f12", "choice", "Which document represents you more accurately?",
     ["Your interview resume", "Your CV", "Equally"]),
    ("f13_profile", "text", "What, if anything, is wrong or missing in your interview resume? (optional)"),
    ("f13_cv", "text", "What, if anything, is wrong or missing in your CV? (optional)"),
    ("f14", "choice", "Was there an experience you wanted to share that the interview questions didn't let you share?",
     ["Yes", "No"]),
    ("f15", "choice", "In the role form at the start, did you find a category for every position you wanted to discuss?",
     ["Yes", "No"]),
]

BLOCKS = {"primary": PRIMARY, "diagnostic": DIAGNOSTIC, "profile_only": PROFILE_ONLY, "final": FINAL}


# ------------------------------------------------------------------ pure logic (tested without Streamlit)

def doc_order(code: str) -> str:
    """Odd trailing number -> resume first, even -> CV first. Codes without a number fall back to a stable checksum."""
    m = re.search(r"(\d+)$", code)
    n = int(m.group(1)) if m else sum(map(ord, code))
    return "profile-first" if n % 2 == 1 else "cv-first"


def screens(order: str) -> list[tuple[str, str]]:
    """List of (document, block). document is "profile", "cv" or "final"."""
    prof = [("profile", "primary"), ("profile", "diagnostic"), ("profile", "profile_only")]
    cv = [("cv", "primary"), ("cv", "diagnostic")]
    body = prof + cv if order == "profile-first" else cv + prof
    return body + [("final", "final")]


def screen_id(screen: tuple[str, str]) -> str:
    return f"{screen[0]}.{screen[1]}"


def answer_key(screen: tuple[str, str], item_id: str) -> str:
    """Items asked of both documents are stored per document (profile_a1, cv_a1); the rest under their own id."""
    return item_id if screen[1] in ("profile_only", "final") else f"{screen[0]}_{item_id}"


def items(screen: tuple[str, str]) -> list[tuple]:
    return BLOCKS[screen[1]]


def required_keys(screen: tuple[str, str]) -> list[str]:
    return [answer_key(screen, it[0]) for it in items(screen) if it[1] != "text"]


def has_text(screen: tuple[str, str]) -> bool:
    return any(it[1] == "text" for it in items(screen))


def is_complete(screen: tuple[str, str], answers: dict) -> bool:
    return all(answers.get(k) not in (None, "") for k in required_keys(screen))


def first_open(order: str, done: set[str]) -> int:
    sc = screens(order)
    for i, s in enumerate(sc):
        if screen_id(s) not in done:
            return i
    return len(sc) - 1


def clicks_total(order: str) -> int:
    """Clicks for a participant who skips every open question: one per required item, one per text-screen button."""
    return sum(len(required_keys(s)) + (1 if has_text(s) else 0) for s in screens(order))


def minutes_left(idx: int, n: int) -> int:
    return max(1, math.ceil((n - idx) * SECONDS_PER_SCREEN / 60))


# ------------------------------------------------------------------ rendering

CSS = """
<style>
/* this page only: use the full width (the interview pages stay centred) */
.block-container, [data-testid="stMainBlockContainer"] {max-width: 1400px; padding-top: 1.2rem;}
.st-key-docpane {height: 80vh !important; max-height: 80vh !important; overflow-y: auto !important;
                 flex-wrap: nowrap !important; border: 1px solid #d9dee3; border-radius: 8px; padding: 8px;
                 background: #f6f7f8;}
.st-key-docpane > div {flex-shrink: 0;}
@media (max-width: 640px) { .st-key-docpane {height: 45vh !important; max-height: 45vh !important;} }
.st-key-qpane .stSegmentedControl {margin-bottom: 0.2rem;}
.st-key-endpane, .st-key-finalpane {animation: rfade .45s ease-out;}
@keyframes rfade {from {opacity: 0; transform: translateY(6px);} to {opacity: 1; transform: none;}}
.rt-steps {display: flex; gap: 6px; flex-wrap: wrap; margin: 4px 0 2px 0;}
.rt-step {font-size: .78rem; padding: 2px 9px; border-radius: 999px; background: #eef1f3; color: #5b6670;}
.rt-step.done {background: #d8ecec; color: #0E6B6B;}
.rt-step.now {background: #0E6B6B; color: #fff; font-weight: 600;}
</style>
"""


@st.cache_data(show_spinner=False, max_entries=64)
def _pages(kind: str, ident: str, pdf: bytes) -> list[bytes]:
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    out = [p.get_pixmap(dpi=110).tobytes("png") for p in doc]
    doc.close()
    return out


def _step_html(sc: list, idx: int) -> str:
    chips = []
    for i, (doc, block) in enumerate(sc):
        name = {"profile": "Resume", "cv": "CV", "final": "Final questions"}[doc]
        cls = "done" if i < idx else ("now" if i == idx else "")
        chips.append(f'<span class="rt-step {cls}">{i + 1} · {name}</span>')
    return '<div class="rt-steps">' + "".join(chips) + "</div>"


def _header(sc: list, idx: int) -> None:
    n = len(sc)
    doc = sc[idx][0]
    about = "" if doc == "final" else f" · rating: **{DOC_LABEL[doc]}**"
    st.markdown(f"**Step {idx + 1} of {n}**{about} · about {minutes_left(idx, n)} min left")
    st.progress(idx / n)
    st.markdown(_step_html(sc, idx), unsafe_allow_html=True)


def _seed(code: str, keys: list[str], answers: dict) -> None:
    """Pre-fills widgets from stored answers (resume after reload, or Back)."""
    for k in keys:
        wk = f"r_{code}_{k}"
        if wk not in st.session_state and k in answers:
            st.session_state[wk] = answers[k]


def _collect(code: str, screen) -> dict:
    out = {}
    for it in items(screen):
        k = answer_key(screen, it[0])
        v = st.session_state.get(f"r_{code}_{k}")
        if v is not None and not (it[1] == "text" and not str(v).strip()):
            out[k] = v.strip() if isinstance(v, str) and it[1] == "text" else v
    return out


def _elapsed(code: str) -> float:
    t0 = st.session_state.get(f"r_{code}_t0")
    return round(time.time() - t0, 1) if t0 else 0.0


def _advance(store, code: str, sc: list, idx: int, force: bool = False) -> None:
    """Saves the screen and moves on. Called from widget callbacks (auto-advance) and from Continue buttons."""
    screen = sc[idx]
    ans = _collect(code, screen)
    if not is_complete(screen, ans):
        return
    if not force and has_text(screen):
        return                                   # text screens move on only with their button
    last = idx == len(sc) - 1
    store.save_rating_screen(code, screen_id(screen), ans, _elapsed(code), complete=last)
    st.session_state[f"r_{code}_idx"] = idx if last else idx + 1
    st.session_state.pop(f"r_{code}_t0", None)


def _widget(code: str, screen, it: tuple, store, sc, idx) -> None:
    k = answer_key(screen, it[0])
    wk = f"r_{code}_{k}"
    kind, label = it[1], it[2]
    if kind in ("scale", "scale_na"):
        opts = SCALE + ([NA] if kind == "scale_na" else [])
        st.segmented_control(label, opts, key=wk, format_func=lambda v: "Not covered" if v == NA else str(v),
                             on_change=_advance, args=(store, code, sc, idx), width="stretch")
    elif kind == "choice":
        st.segmented_control(label, it[3], key=wk, on_change=_advance, args=(store, code, sc, idx), width="stretch")
    else:
        st.text_area(label, key=wk, height=90)


def _questions(store, code: str, sc: list, idx: int, done: set[str]) -> None:
    screen = sc[idx]
    doc = screen[0]
    if idx == 0:
        st.info(f"{len(sc)} short steps, about {minutes_left(0, len(sc))} minutes. Click one answer per statement; "
                "the next step opens by itself. Your answers are saved after every step.")
    if screen[1] == "final":
        st.markdown("Last step. Almost done.")
    elif screen[1] == "profile_only":
        st.markdown("Two statements and one optional question, only about your interview resume.")
    if any(it[1].startswith("scale") for it in items(screen)):
        st.caption(SCALE_NOTE + (" · " + NA_NOTE if screen[1] == "diagnostic" else ""))
    for it in items(screen):
        _widget(code, screen, it, store, sc, idx)
    ans = _collect(code, screen)
    nav = st.columns([1, 2, 2], vertical_alignment="center")
    if idx > 0 and nav[0].button("← Back", key=f"r_{code}_back_{idx}", type="tertiary"):
        st.session_state[f"r_{code}_idx"] = idx - 1
        st.session_state.pop(f"r_{code}_t0", None)
        st.rerun()
    complete = is_complete(screen, ans)
    if has_text(screen):
        typed = any(answer_key(screen, it[0]) in ans for it in items(screen) if it[1] == "text")
        last = idx == len(sc) - 1
        label = "Submit" if last else ("Continue" if typed else "Skip and continue")
        if nav[1].button(label, key=f"r_{code}_next_{idx}", type="primary", disabled=not complete):
            _advance(store, code, sc, idx, force=True)
            st.rerun()
        if not complete:
            nav[2].caption(f"{len(required_keys(screen)) - sum(k in ans for k in required_keys(screen))} "
                           "answer(s) left on this step")
    elif screen_id(screen) in done and complete:
        if nav[1].button("Continue →", key=f"r_{code}_fwd_{idx}", type="primary"):
            _advance(store, code, sc, idx, force=True)
            st.rerun()


def _end(code: str, profile: dict) -> None:
    with st.container(key="endpane"):
        st.success("**Thank you, you are done.** Your answers have been saved.")
        st.markdown("You can download your interview resume below. It is the same document the recruiters will see, "
                    "shown under your participant code.")
        st.download_button("Download your interview resume (PDF)", profile["pdf"],
                           file_name=f"{code} - interview resume.pdf", mime="application/pdf", type="primary",
                           key=f"r_{code}_dl")


def render_rating(store, raw_code: str, allowed_codes: set[str]) -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    code = (raw_code or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9-]{2,20}", code) or (allowed_codes and code not in allowed_codes):
        st.markdown("### Questionnaire")
        st.error("This link is not valid. Please check the link you received.")
        return
    sess = store.get_session(code)
    cvrow = store.get_cv(code)
    rating = store.get_rating(code)
    profile = store.get_profile(rating["profile_id"]) if rating else store.get_approved_profile(code)
    if not sess or sess["status"] != "complete" or not cvrow or not profile:
        st.markdown("### Questionnaire")
        st.info("Your interview resume is not ready yet. The study lead will send you a message when it is; "
                "please use this link again then.")
        return

    if rating is None:
        rating = store.start_rating(code, INSTRUMENT, doc_order(code), profile["id"])
    order = rating["doc_order"]
    sc = screens(order)
    done = set((rating["screens"] or {}).keys())

    if rating["status"] == "complete":
        st.markdown("### Questionnaire")
        _end(code, profile)
        return

    ik = f"r_{code}_idx"
    if ik not in st.session_state:
        st.session_state[ik] = first_open(order, done)
    idx = st.session_state[ik]
    if f"r_{code}_t0" not in st.session_state:
        st.session_state[f"r_{code}_t0"] = time.time()
    screen = sc[idx]
    _seed(code, [answer_key(screen, it[0]) for it in items(screen)], rating["answers"] or {})

    st.markdown("### Questionnaire")
    _header(sc, idx)

    if screen[0] == "final":
        _, mid, _ = st.columns([1, 2, 1])
        with mid, st.container(key="finalpane"):
            with st.container(key="qpane"):
                _questions(store, code, sc, idx, done)
        return

    pdf = profile["pdf"] if screen[0] == "profile" else cvrow["pdf"]
    ident = f"profile-{profile['id']}" if screen[0] == "profile" else f"cv-{code}"
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown(f"**{DOC_LABEL[screen[0]]}**")
        with st.container(key="docpane"):
            for png in _pages(screen[0], ident, pdf):
                st.image(png, width="stretch")
    with right, st.container(key="qpane"):
        st.markdown(f"**Questions about {DOC_LABEL[screen[0]].replace('Your', 'your')}**")
        _questions(store, code, sc, idx, done)
