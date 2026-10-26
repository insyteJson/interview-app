"""Participant interview app (Streamlit).

Participant link:  https://<your-app>/?code=P01
Questionnaire:     https://<your-app>/?rate=P01   (send only once the profile is approved)
Recruiter study:   https://<your-app>/?review=inv-...   (invite links are created in the researcher view)
Researcher view:   https://<your-app>/?admin=1
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone

import streamlit as st

st.set_page_config(page_title="Interview", page_icon="\U0001F4AC", layout="centered")
st.markdown(
    "<style>#MainMenu{visibility:hidden} footer{visibility:hidden} [data-testid='stToolbar']{display:none} "
    "[data-testid='stHeader'],[data-testid='stDecoration']{display:none !important} "
    "[data-testid='stMainBlockContainer'],.block-container{padding-top:1.5rem !important}</style>",
    unsafe_allow_html=True,
)

# Shown immediately, before the heavy imports and the database connection below.
_boot = st.empty()
with _boot.container():
    st.title("Interview")
    st.info("Loading, one moment…")

import cv  # noqa: E402
import engine  # noqa: E402
import roles  # noqa: E402
import roles_ui  # noqa: E402
from classifier import Classifier, ClassifierError  # noqa: E402
from storage import SCHEMA_VERSION, ConflictError, RunCache, Store  # noqa: E402


# ------------------------------------------------------------------ config

def cfg(key: str, default=None):
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:  # no secrets file
        pass
    return os.environ.get(key, default)


MODEL = cfg("MODEL", "claude-opus-5-5")
MAX_POSITIONS = int(cfg("MAX_POSITIONS", 0) or 0)
ALLOWED_CODES = {c.strip().upper() for c in str(cfg("ALLOWED_CODES", "")).split(",") if c.strip()}
EFFORT = str(cfg("EFFORT", "medium")).strip().lower() or "medium"


@st.cache_resource(show_spinner="Loading the interview…")
def get_store(schema_version: str) -> Store:
    """Cached per schema version: after a code update that changes storage, a new Store is built."""
    return Store(cfg("DATABASE_URL", "sqlite:///interviews.db"))


@st.cache_resource(show_spinner=False)
def get_classifier():
    key = cfg("ANTHROPIC_API_KEY")
    if not key:
        return None
    return Classifier(api_key=key, model=MODEL, effort=EFFORT)


def load_consent() -> str:
    path = os.path.join(os.path.dirname(__file__), "consent.md")
    with open(path, encoding="utf-8") as f:
        return f.read()


def consent_version(text: str) -> str:
    """Version of the consent text actually shown, read from its header, so the stored record cannot drift."""
    m = re.search(r"Version\s+(\d+(?:\.\d+)*)", text)
    return (m.group(1) if m else "unknown") + ("-draft" if "[CONFIRM" in text else "")


try:
    store = RunCache(get_store(SCHEMA_VERSION))     # identical reads within one click are answered once
except Exception:  # database unreachable or misconfigured: log details, show participants nothing technical
    logging.getLogger("interview").exception("Database connection failed")
    _boot.empty()
    st.error("The interview is not available right now. Please contact the researcher.")
    st.stop()
classifier = st.session_state.get("_classifier_override") or get_classifier()
_boot.empty()  # loading notice is done


# ------------------------------------------------------------------ admin route

if st.query_params.get("admin"):
    from admin import render_admin
    from storage import SHARED_TTL
    store = RunCache(store._store, shared_ttl=SHARED_TTL)   # researcher view: reuse data across clicks
    def make_generator():
        key = cfg("ANTHROPIC_API_KEY")
        if not key:
            return None
        from profile_gen import ProfileGenerator
        return ProfileGenerator(api_key=key, model=MODEL, effort=EFFORT)
    render_admin(store, cfg("ADMIN_PASSWORD"), st.session_state.get("_generator_override") or make_generator,
                 base_url=cfg("APP_URL"), cfg=cfg)
    st.stop()


# ------------------------------------------------------------------ recruiter study (invite links, ?review=inv-...)

if st.query_params.get("review"):
    from review import render_review
    render_review(store, st.query_params.get("review"), cfg("APP_URL"), cfg=cfg)
    st.stop()


# ------------------------------------------------------------------ questionnaire route (separate link, ?rate=P01)

if st.query_params.get("rate"):
    from rating import render_rating
    render_rating(store, st.query_params.get("rate"), ALLOWED_CODES)
    st.stop()


# ------------------------------------------------------------------ participant

st.title("Interview")

code = (st.query_params.get("code") or st.session_state.get("code") or "").strip().upper()
if not code:
    with st.form("code_form"):
        entered = st.text_input("Please enter your participant code", key="code_input")
        if st.form_submit_button("Continue"):
            st.session_state["code"] = entered.strip().upper()
            st.rerun()
    st.stop()

if not re.fullmatch(r"[A-Z0-9-]{2,20}", code) or (ALLOWED_CODES and code not in ALLOWED_CODES):
    st.error("This participant code is not valid. Please check the link you received.")
    st.stop()

if classifier is None:
    st.error("The interview is not available right now. Please contact the researcher.")
    st.stop()

sess = store.get_session(code)

# --- consent and start
if sess is None:
    consent_text = load_consent()
    consent_slot = st.empty()  # cleared the moment "Start" is clicked, so nothing lingers over the next page
    consent_box = consent_slot.container()
    with consent_box:
        if "[CONFIRM" in consent_text:
            st.warning("DRAFT consent text: unresolved [CONFIRM] items. Do not send this link to participants yet.")
        st.markdown(consent_text)
        st.markdown("**Please tick each statement. Statements 1 to 4 are required to take part; statement 5 is optional.**")
        # A form: ticking a box does not reload the page (which jumped back to the top); checked on submit.
        with st.form("consent_form", border=False):
            c1 = st.checkbox("1. I have read and understood the information above and have had the opportunity to ask questions.", key="consent_1")
            c2 = st.checkbox("2. I consent to my answers, my profile, my questionnaire answers and my anonymised CV being processed for this study as described.", key="consent_2")
            c3 = st.checkbox("3. I explicitly consent to my answers being transferred to Anthropic PBC and to the Streamlit hosting service in the United States, as described in section 6. I have been informed of the risks of this transfer.", key="consent_3")
            c4 = st.checkbox("4. I consent to my profile and anonymised CV being shown, under my participant code, to up to 10 recruiters for research purposes.", key="consent_4")
            c5 = st.checkbox("5. (optional) I consent to short anonymised quotations from my interview being included in the thesis.", key="consent_5")
            st.caption("I understand that I can withdraw my consent at any time.")
            start = st.form_submit_button("Start the interview", type="primary")
        if start and not (c1 and c2 and c3 and c4):
            st.error("Please tick statements 1 to 4 to take part.")
    if start and c1 and c2 and c3 and c4:
        consent_slot.empty()
        with st.spinner("Preparing your interview…"):
            state = engine.new_state(code, MODEL, MAX_POSITIONS)
            state["consent"] = {"version": consent_version(consent_text), "statements": [1, 2, 3, 4] + ([5] if c5 else []),
                                "at": datetime.now(timezone.utc).isoformat()}
            state, res = engine.start(state)
            try:
                store.create_session(code, state, {
                    "turn": 0, "answer_raw": "", "question_shown": res["output"], "rule": res["rule"],
                    "qid": res["qid"], "phase_before": "start", "classification": None, "meta": None,
                    "accepted": 0, "declined": 0, "audit": "", "audit_note": "",
                })
            except Exception:
                pass  # created concurrently by a double click; the reload shows it
        st.rerun()
    st.stop()

state = sess["state"]


def show_transcript() -> None:
    for row in store.get_turns(code):
        if row["turn"] > 0:
            with st.chat_message("user"):
                st.markdown(row["answer_raw"])
        with st.chat_message("assistant"):
            st.markdown(row["question_shown"].replace("\n- ", "\n\n- ") if row["turn"] == 0 else row["question_shown"])


# --- conversation so far (once complete, it moves into an expander below the CV step)
if state["status"] != "complete":
    show_transcript()
if state["status"] == "aborted":
    st.stop()


def cv_step(first: bool = False) -> None:
    if not first:
        st.divider()
    st.subheader("Last step: your CV")
    saved = store.get_cv(code)
    if saved:
        st.success("Your anonymised CV has been received. Thank you, you are done.")
        return
    st.markdown(
        "Please upload your CV as a **PDF**. The page removes your name, contact details, links, photo and any "
        "anything you list below (for example an employer you prefer to hide) by **blacking them out**; everything "
        "else stays as it is. Company names you do not list stay visible, as they do in your resume. "
        "Your original file is processed in memory only and **is not stored**; only the blacked-out version is "
        "saved. You will see every page before you submit it."
    )
    prev = st.session_state.get("cv_result")
    if prev is None:
        name = st.text_input("Your full name, as it appears on the CV", key="cv_name")
        others = st.text_area(
            "Anything else to black out, one per line (optional; for example your city or an employer you prefer to hide)",
            key="cv_others", height=120)
        up = st.file_uploader("CV (PDF, up to 6 pages)", type=["pdf"], key="cv_file")
        if st.button("Black out and preview", type="primary", disabled=not (up and name.strip())):
            with st.spinner("Processing your CV…"):
                try:
                    st.session_state["cv_result"] = cv.redact_pdf(up.getvalue(), name, others)
                except cv.CvError as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
        return
    rep = prev.report
    b = rep["blacked_out"]
    st.info(f"Blacked out: {b['own_terms']} name/term matches, {b['email']} email, {b['phone']} phone, "
            f"{b['link']} web links, {b['images']} images (photos or logos); {rep['links_removed']} clickable links removed.")
    if rep["terms_not_found"]:
        st.warning("These entries were not found in the CV (check spelling if you expected them): "
                   + ", ".join(rep["terms_not_found"]))
    if rep["leftovers"]:
        st.error("Some entries could still be read in the result. Please start over and add them under "
                 "'Anything else to black out', or send your CV to the researcher directly.")
    for i, png in enumerate(prev.previews, 1):
        st.image(png, caption=f"Page {i}", width="stretch")
    ok = st.checkbox("I looked at every page. My name, contact details and photo, and everything I listed, are blacked out.",
                     key="cv_ok", disabled=bool(rep["leftovers"]))
    c1, c2 = st.columns(2)
    if c1.button("Submit this CV", type="primary", disabled=not ok):
        store.save_cv(code, prev.pdf, cv.storable_report(rep))
        try:                                     # auto-★ for the recruiter pool; must never disturb the participant
            import autostar
            if autostar.evaluate(store, code):
                import notify
                reason = store.list_auto_decisions().get(code, {}).get("reason", "")
                s = notify.settings(cfg)
                notify.send_in_background(s, notify.star_message(s, code, reason) if s else None)
        except Exception:
            logging.getLogger("interview").exception("Auto-star check failed")
        st.session_state.pop("cv_result", None)
        st.rerun()
    if c2.button("Start over"):
        st.session_state.pop("cv_result", None)
        st.rerun()



# --- interview finished: the CV step comes first. Streamlit jumps to the top of the page when the chat box
# disappears, so a CV step below a long transcript was never seen (two sessions completed without a CV).
if state["status"] == "complete":
    if not store.get_cv(code):
        st.success("**The interview questions are complete. One last step: please upload your CV below.** "
                   "Your participation is finished once your CV has been submitted.")
    cv_step(first=True)
    with st.expander("Your interview answers (for reference)"):
        show_transcript()
    st.stop()

# --- role form (first step after consent; no model involved)
@st.fragment
def role_form_step() -> None:
    """Runs on its own: adding or removing a row reruns only this form, not the whole page."""
    max_roles = min(MAX_POSITIONS or roles.MAX_ROLES, roles.MAX_ROLES)
    with st.chat_message("user"):
        rows = roles_ui.render(max_roles)
    if rows is None:
        return
    with st.spinner("Saving your list…"):
        positions, _ = roles.normalise(rows)
        new_state, res = engine.set_positions(state, positions)
        row = {
            "turn": new_state["turn"], "answer_raw": roles.summary(new_state["positions"]),
            "question_shown": res["output"], "rule": res["rule"], "qid": res["qid"], "phase_before": "form",
            "classification": None, "meta": {"input": "role form", "rows": rows},
            "accepted": 0, "declined": 0, "audit": "", "audit_note": "",
        }
        try:
            store.save_turn(code, sess["version"], new_state, row)
        except ConflictError:
            pass  # submitted twice (double click); the reload shows the saved version
    roles_ui.clear()
    st.rerun(scope="app")


if state["phase"] == "form":
    role_form_step()
    st.stop()

if st.session_state.get("failed_answer"):
    st.warning("Your last answer didn't go through. Here it is so you can copy it and send it again:")
    st.code(st.session_state.pop("failed_answer"), language=None, wrap_lines=True)

answer = st.chat_input("Your answer")
st.caption("Tip: you can dictate with your keyboard's microphone (Windows: Win + H, Mac: press Fn twice, "
           "phone: microphone key on the keyboard).")

if answer and answer.strip():
    answer = answer.strip()
    with st.chat_message("user"):
        st.markdown(answer)
    with st.chat_message("assistant"):
        with st.spinner("Reading your answer, one moment…"):
            try:
                cls, meta = classifier.classify(state, answer)
            except ClassifierError:
                st.session_state["failed_answer"] = answer
                st.rerun()
            new_state, res = engine.step(state, answer, cls)
            row = {
                "turn": new_state["turn"], "answer_raw": answer, "question_shown": res["output"],
                "rule": res["rule"], "qid": res["qid"], "phase_before": state["phase"],
                "classification": cls, "meta": meta,
                "accepted": int(res["accepted"]), "declined": int(res["declined"]), "audit": "", "audit_note": "",
            }
            try:
                store.save_turn(code, sess["version"], new_state, row)
            except ConflictError:
                st.session_state["failed_answer"] = answer
    st.rerun()
