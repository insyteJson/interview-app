"""Recruiter study (DO1), on the same app under its own link.

Access only through personal invite links (?review=inv-XXXX), single-use, created by the researcher or by a
recruiter ("share with a colleague"). After consent the recruiter gets a code (R01, R02, ...) and a personal link
(?review=R04.<key>) to continue later.

Per candidate: Document A (one PDF + 10 questions), Document B (same), comparison (both PDFs side by side).
Which candidate comes next is decided by a queue over the researcher's favourites (only sessions that are ready);
no candidate is ever shown to more than 10 recruiters. The interview pipeline and the candidate page are untouched.
"""

from __future__ import annotations

import hmac
import os
import random
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import streamlit as st

import guide
from rating import _pages
from storage import InviteError

INSTRUMENT = "do1-recruiter-v1"
CONSENT_FILE = "recruiter_consent.md"
SIGNUP_VERSION = "signup-1.0"
MIN_CANDIDATES = 3
MAX_PER_CANDIDATE = 10
RESERVE_HOURS = 24
MAX_SHARES = 3
SECONDS_PER_CANDIDATE = 8 * 60


def T(en: str, de: str) -> str:
    """English text with its German translation underneath, smaller and grey."""
    return f"{en}<br><span class='rv-de'>{de}</span>"


def A(en: str, de: str) -> str:
    """For alert boxes (no HTML allowed there): English, then German in italics on the next line."""
    return f"{en}  \n*{de}*"


def md(en: str, de: str) -> None:
    st.markdown(T(en, de), unsafe_allow_html=True)


# ------------------------------------------------------------------ questions (the only place wording lives)

AGREE = ("1 = strongly disagree · 7 = strongly agree", "1 = stimme gar nicht zu · 7 = stimme voll zu")
AMOUNT = ("1 = nothing · 7 = a great deal", "1 = gar nichts · 7 = sehr viel")
COVER_LEAD = ("How much does this document tell you about …", "Wie viel sagt Ihnen dieses Dokument darüber, …")

GROUPS = [
    (AGREE, None, [
        ("u1", "This document lets me assess this candidate well.",
         "Mit diesem Dokument kann ich diese Person gut einschätzen."),
        ("u2", "This document lets me assess this candidate with little effort.",
         "Mit diesem Dokument kann ich diese Person mit wenig Aufwand einschätzen."),
        ("u3", "This document lets me assess this candidate effectively.",
         "Mit diesem Dokument kann ich diese Person effektiv einschätzen."),
        ("u4", "This document is useful for assessing this candidate.",
         "Dieses Dokument ist nützlich, um diese Person einzuschätzen."),
    ]),
    (AMOUNT, COVER_LEAD, [
        ("c1", "… how well this candidate does their work?", "… wie gut diese Person ihre Arbeit macht?"),
        ("c2", "… how this candidate acts when standards or commitments are at stake?",
         "… wie diese Person handelt, wenn Standards oder Verpflichtungen auf dem Spiel stehen?"),
        ("c3", "… how this candidate acts when others need help?", "… wie diese Person handelt, wenn andere Hilfe brauchen?"),
        ("c4", "… how this candidate typically works, not only at their best?",
         "… wie diese Person normalerweise arbeitet, nicht nur in ihren besten Momenten?"),
    ]),
    (AGREE, None, [
        ("r1", "I can rely on what this document says about this candidate.",
         "Ich kann mich auf das verlassen, was dieses Dokument über diese Person sagt."),
        ("r2", "This document helps me distinguish this candidate from other candidates.",
         "Dieses Dokument hilft mir, diese Person von anderen Bewerbern zu unterscheiden."),
    ]),
]
DOC_ITEMS = [it[0] for g in GROUPS for it in g[2]]

PREF = ("Which document would you rather use to assess this candidate?",
        "Welches Dokument würden Sie lieber nutzen, um diese Person einzuschätzen?")
PREF_OPTIONS = ["Document A", "Document B", "Equally / Gleich"]
MISSING = ("What would you need to know that neither document tells you? (optional)",
           "Was müssten Sie wissen, das Ihnen keines der beiden Dokumente verrät? (optional)")

EXPERIENCE = ("How many years of experience do you have in recruiting or screening candidates?",
              "Wie viele Jahre Erfahrung haben Sie im Recruiting oder in der Vorauswahl von Bewerbern?")
EXPERIENCE_OPTIONS = ["<2", "2–5", "6–10", ">10"]
DOMAIN = ("Which domain do you mainly hire for? (e.g. IT, finance)",
          "Für welchen Bereich stellen Sie hauptsächlich ein? (z. B. IT, Finanzen)")
COMMENT = ("What, if anything, would stop you from using a document like the interview resume or the CV in "
           "practice? (optional)",
           "Was würde Sie gegebenenfalls davon abhalten, ein Dokument wie den Interview-Lebenslauf oder den "
           "klassischen Lebenslauf in der Praxis zu nutzen? (optional)")

CONSENT_STATEMENTS = [
    ("I have read and understood the information above, and I have professional experience in recruiting or "
     "screening candidates.",
     "Ich habe die obigen Informationen gelesen und verstanden und habe berufliche Erfahrung im Recruiting oder in "
     "der Vorauswahl von Bewerbern."),
    ("I consent to my ratings, comments and the technical records being processed for this study as described.",
     "Ich willige ein, dass meine Bewertungen, Kommentare und die technischen Aufzeichnungen wie beschrieben für "
     "diese Studie verarbeitet werden."),
    ("I explicitly consent to my answers passing through the Streamlit hosting service in the United States, as "
     "described in section 7. I have been informed of the risks of this transfer.",
     "Ich willige ausdrücklich ein, dass meine Antworten wie in Abschnitt 7 beschrieben über den Hosting-Dienst "
     "Streamlit in den USA laufen. Ich wurde über die Risiken dieser Übermittlung informiert."),
    ("I will keep the candidate documents confidential as described in section 4: I will not save, copy, screenshot "
     "or share them, I will not try to identify any candidate, and I will skip any candidate I recognise.",
     "Ich behandle die Dokumente der Bewerber wie in Abschnitt 4 beschrieben vertraulich: Ich speichere, kopiere, "
     "fotografiere oder teile sie nicht, versuche nicht, Personen zu identifizieren, und überspringe jede Person, "
     "die ich erkenne."),
    ("(optional) I consent to short anonymised quotations from my comments being included in the thesis.",
     "(optional) Ich willige ein, dass kurze anonymisierte Zitate aus meinen Kommentaren in der Arbeit verwendet "
     "werden."),
]

# after the 3rd candidate (once), then rotating lines after every further candidate
MILESTONE = ("That's the 3 you signed up for. Thank you, this really helps me out! If you have another ~8 minutes, "
             "every extra candidate makes my results more reliable.",
             "Das waren die 3. Vielen Dank, das hilft mir wirklich sehr! Wenn Sie noch etwa 8 Minuten haben: Jede "
             "weitere Person macht meine Ergebnisse belastbarer.")
ROTATING = [
    ("{n} candidates done. Honestly, this is a huge help for my thesis. Up for one more?",
     "{n} Personen geschafft. Ehrlich, das ist eine riesige Hilfe für meine Arbeit. Noch eine?"),
    ("With only a handful of recruiters in this study, every extra rating makes a real difference. Thank you!",
     "Bei nur wenigen Recruitern in dieser Studie macht jede weitere Bewertung einen echten Unterschied. Danke!"),
    ("You're on a roll! The next candidate is ready whenever you are, or stop here with my full thanks.",
     "Sie sind gut dabei! Die nächste Person ist bereit, oder Sie hören hier auf, mit meinem vollen Dank."),
]
THANKS = ("Thank you so much for your time, it means a lot.", "Vielen Dank für Ihre Zeit, das bedeutet mir viel.")
PROTOTYPE = ("This interview resume is a research prototype. Your ratings and comments help decide how it develops "
             "further.",
             "Dieser Interview-Lebenslauf ist ein Forschungsprototyp. Ihre Bewertungen und Kommentare helfen zu "
             "entscheiden, wie er weiterentwickelt wird.")
NEXT_STEPS = ("This study is the first step. If the results are promising, I want to develop it further into a "
              "recruiting product where candidates show how they work, through real situations instead of polished "
              "self-descriptions, so recruiters see patterns, not buzzwords. I'd love to hear what you think along "
              "the way.",
              "Diese Studie ist der erste Schritt. Wenn die Ergebnisse vielversprechend sind, möchte ich daraus ein "
              "Recruiting-Produkt entwickeln, in dem Bewerber zeigen, wie sie arbeiten: durch echte Situationen statt "
              "geschliffener Selbstbeschreibungen. So sehen Recruiter Muster statt Schlagworte. Ich freue mich, "
              "unterwegs Ihre Meinung zu hören.")
SIGNUP_CONSENT = ("I agree that Jason Harris stores my email address to send me occasional updates about this "
                  "project. It is stored separately from my study answers, and I can unsubscribe at any time by "
                  "replying to any email or writing to jason.jh83@gmail.com.",
                  "Ich bin einverstanden, dass Jason Harris meine E-Mail-Adresse speichert, um mir gelegentlich "
                  "Neuigkeiten zu diesem Projekt zu schicken. Sie wird getrennt von meinen Studienantworten "
                  "gespeichert; ich kann mich jederzeit abmelden.")


# ------------------------------------------------------------------ queue (pure logic, tested without Streamlit)

def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def ready_pool(store) -> list[dict]:
    """Favourites with a complete interview, an approved profile and a CV marked as checked."""
    favs = store.list_favorites()
    if not favs:
        return []
    done = {s["id"] for s in store.list_sessions() if s["status"] == "complete"}
    profs, cvs = store.profiles_meta(), store.cvs_meta()
    out = []
    for sid in sorted(favs):
        prof = next((p for p in profs.get(sid, []) if p["approved"] and p["ok"] and p["has_pdf"]), None)
        cv = cvs.get(sid)
        if sid in done and prof and cv and cv["checked"]:
            out.append({"session_id": sid, "profile_id": prof["id"]})
    return out


def pick_next(store, code: str, rng=random, now: datetime | None = None) -> dict | None:
    """Least-rated ready favourite this recruiter has not seen; ties at random. A candidate already shown to
    MAX_PER_CANDIDATE recruiters (any status) is never shown again. Document order: whichever order this candidate
    has had less often."""
    now = now or datetime.now(timezone.utc)
    allr = store.list_reviews()
    mine = {r["session_id"] for r in allr if r["recruiter"] == code}

    def active(r):
        return r["status"] == "complete" or (
            r["status"] == "in_progress" and _aware(r["updated_at"]) > now - timedelta(hours=RESERVE_HOURS))

    cands = []
    for p in ready_pool(store):
        if p["session_id"] in mine:
            continue
        rs = [r for r in allr if r["session_id"] == p["session_id"]]
        if len(rs) >= MAX_PER_CANDIDATE:
            continue
        cands.append((p, [r for r in rs if active(r)]))
    if not cands:
        return None
    low = min(len(a) for _, a in cands)
    p, act = rng.choice([c for c in cands if len(c[1]) == low])
    pf = sum(r["doc_order"] == "profile-first" for r in act)
    cf = len(act) - pf
    order = "profile-first" if pf < cf else "cv-first" if cf < pf else rng.choice(["profile-first", "cv-first"])
    return {"session_id": p["session_id"], "profile_id": p["profile_id"], "doc_order": order}


def screens(order: str) -> list[tuple[str, str]]:
    first, second = ("profile", "cv") if order == "profile-first" else ("cv", "profile")
    return [("A", first), ("B", second), ("compare", "")]


def doc_keys(doc: str) -> list[str]:
    return [f"{doc}_{i}" for i in DOC_ITEMS]


def completed(store, code: str) -> int:
    return sum(r["status"] == "complete" for r in store.list_reviews(recruiter=code))


# ------------------------------------------------------------------ rendering helpers

CSS = """
<style>
.block-container, [data-testid="stMainBlockContainer"] {max-width: 1500px; padding-top: 1rem;}
/* text pages read better at a narrower width */
.st-key-rv_narrow {max-width: 860px; margin: 0 auto;}
/* document panes */
[class*="st-key-docpane"] {height: 78vh !important; max-height: 78vh !important; overflow-y: auto !important;
    flex-wrap: nowrap !important; border: 1px solid #d9dee3; border-radius: 10px; padding: 8px; background: #f6f7f8;}
[class*="st-key-docpane"] > div {flex-shrink: 0;}
[class*="st-key-docpanecmp"] {height: 60vh !important; max-height: 60vh !important;}
@media (max-width: 640px) { [class*="st-key-docpane"] {height: 45vh !important; max-height: 45vh !important;} }
@media (min-width: 641px) {
  [data-testid="stHorizontalBlock"]:has([class*="st-key-docpane"]) {align-items: flex-start;}
  [data-testid="stColumn"]:has(> div [class*="st-key-docpane"]):not(:has([class*="st-key-docpanecmp"])) {
      position: sticky; top: 1rem;}
}
.st-key-rv_consenttext {height: 46vh; overflow-y: auto; border: 1px solid #d9dee3; border-radius: 10px;
    padding: 4px 18px; background: #fafbfc;}
/* typography */
.rv-de {color: #6b7480; font-size: .86em;}
.rv-title {font-size: 1.15rem; font-weight: 700; margin: 0;}
.rv-title .rv-de {font-weight: 400;}
p.rv-q {font-weight: 500; margin: 0 !important; padding-bottom: 1.1rem; line-height: 1.35;}
.rv-group {font-size: .78rem; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; color: #0E6B6B;}
.rv-anchors {display: flex; justify-content: space-between; font-size: .76rem; color: #6b7480; margin-top: -2px;}
.rv-count {font-size: .85rem; color: #0E6B6B; font-weight: 600;}
.st-key-rv_q [data-testid="stVerticalBlock"] {gap: 0.5rem;}
/* chips and dots */
.rv-steps {display: flex; gap: 6px; flex-wrap: wrap; align-items: center;}
.rv-chip {font-size: .8rem; padding: 3px 11px; border-radius: 999px; background: #eef1f3; color: #5b6670;}
.rv-chip.done {background: #d8ecec; color: #0E6B6B;}
.rv-chip.now {background: #0E6B6B; color: #fff; font-weight: 600;}
.rv-doclabel {display: inline-block; font-weight: 700; padding: 3px 12px; border-radius: 8px; background: #0E6B6B;
    color: #fff; font-size: .9rem;}
.rv-dots {font-size: .82rem; color: #5b6670;}
.rv-dot {display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: #d9dee3; margin-right: 3px;
    vertical-align: -1px;}
.rv-dot.on {background: #0E6B6B;}
.rv-hero {text-align: center; padding: 18px 8px 6px 8px;}
.rv-hero .big {font-size: 2.2rem; line-height: 1;}
.rv-hero h2 {margin: 8px 0 2px 0; padding: 0;}
.rv-pill {display: inline-block; font-size: .78rem; padding: 2px 10px; border-radius: 999px; background: #eef1f3;
    color: #5b6670;}
.st-key-rv_end, .st-key-rv_between {animation: rfade .45s ease-out;}
@keyframes rfade {from {opacity: 0; transform: translateY(6px);} to {opacity: 1; transform: none;}}
</style>
"""


def _base_url(base: str | None) -> str:
    if base:
        return base.rstrip("/") + "/"
    try:
        url = st.context.url or ""
        return url.split("?")[0]
    except Exception:
        return ""


def _personal_link(rec: dict, base: str | None) -> str:
    return f"{_base_url(base)}?review={rec['code']}.{rec['key']}"


def _invite_link(token: str, base: str | None) -> str:
    return f"{_base_url(base)}?review={token}"


def html(s: str) -> None:
    st.markdown(s, unsafe_allow_html=True)


def _scroll_top_on_change(view: str) -> None:
    """Jump back to the top whenever the screen changes (Streamlit keeps the old scroll position)."""
    if st.session_state.get("rv_view") == view:
        return
    st.session_state["rv_view"] = view
    try:
        import streamlit.components.v1 as components
        components.html(f"<script>/* {view} */ const m = window.parent.document.querySelector('[data-testid=stMain]');"
                        "if (m) m.scrollTo({top: 0, behavior: 'instant'});</script>", height=0)
    except Exception:
        pass


def _chips(labels: list[str], now: int) -> str:
    return "<div class='rv-steps'>" + "".join(
        f"<span class='rv-chip {'done' if i < now else 'now' if i == now else ''}'>{'✓ ' if i < now else ''}{x}</span>"
        for i, x in enumerate(labels)) + "</div>"


def _dots(done: int) -> str:
    n = max(MIN_CANDIDATES, done)
    dots = "".join(f"<span class='rv-dot {'on' if i < done else ''}'></span>" for i in range(n))
    if done < MIN_CANDIDATES:
        txt = f"{done} of {MIN_CANDIDATES} candidates done <span class='rv-de'>· {done} von {MIN_CANDIDATES} geschafft</span>"
    else:
        txt = f"{done} candidates done, thank you! <span class='rv-de'>· {done} geschafft, danke!</span>"
    return f"<span class='rv-dots'>{dots}&nbsp; {txt}</span>"


def _topbar(rec: dict | None = None, base_url=None, open_link: bool = False) -> None:
    left, right = st.columns([5, 2], vertical_alignment="center")
    with left:
        html("<p class='rv-title'>Recruiter study <span class='rv-de'>· Recruiter-Studie</span></p>")
    if rec:
        with right:
            with st.popover(f"Your code {rec['code']} · personal link", icon=":material/link:", width="stretch"):
                md("**Save this link.** It is the only way to continue later or to withdraw your data.",
                   "**Speichern Sie diesen Link.** Nur damit können Sie später weitermachen oder Ihre Daten löschen "
                   "lassen.")
                st.code(_personal_link(rec, base_url), language=None, wrap_lines=True)
        if open_link:
            st.info(A(f"**Your code is {rec['code']}.** Save your personal link (button at the top right) to "
                      "continue later.",
                      f"Ihr Code ist {rec['code']}. Speichern Sie Ihren persönlichen Link (Schaltfläche oben rechts), "
                      "um später weiterzumachen."), icon=":material/bookmark:")
    st.write("")


@st.cache_data(show_spinner=False)
def _tour_image(k: int) -> bytes:
    return guide.step_image(k)


def _tour_body(code: str, on_finish, scope: str = "app") -> None:
    """The guide. Back/Next change the step in a callback, so inside a partial refresh only the guide re-renders."""
    kk = f"rv_{code}_tour"
    k = st.session_state.get(kk, 0)
    n = len(guide.STEPS)
    step = guide.STEPS[k]

    def go(to: int):
        st.session_state[kk] = to

    left, right = st.columns([3, 2], gap="large")
    with left:
        st.image(_tour_image(k), width="stretch")
    with right:
        html("<div class='rv-steps'>" + "".join(
            f"<span class='rv-dot {'on' if i <= k else ''}'></span>" for i in range(n))
            + f"&nbsp;<span class='rv-dots'>Step {k + 1} of {n} <span class='rv-de'>· Schritt {k + 1} von {n}"
              "</span></span></div>")
        st.write("")
        html(f"<p class='rv-title'>{step['title'][0]}<br><span class='rv-de'>{step['title'][1]}</span></p>")
        st.markdown(step["text"][0])
        html(f"<span class='rv-de'>{step['text'][1]}</span>")
        st.write("")
        c = st.columns(2)
        if k > 0:
            c[0].button("← Back · Zurück", key=f"{kk}_back", width="stretch", on_click=go, args=(k - 1,))
        if k < n - 1:
            c[1].button("Next · Weiter →", key=f"{kk}_next", type="primary", width="stretch", on_click=go,
                        args=(k + 1,))
            if st.button("Skip the guide · Anleitung überspringen", key=f"{kk}_skip", type="tertiary"):
                st.session_state[kk] = 0
                on_finish()
        elif c[1].button("Start rating · Los geht's", key=f"{kk}_done", type="primary", width="stretch"):
            st.session_state[kk] = 0
            on_finish()


@st.fragment
def _tour_fragment(store, code: str) -> None:
    def finish():
        store.update_recruiter(code, guide_done=1)
        st.session_state.pop("rv_new", None)
        st.rerun()
    _tour_body(code, finish)


@st.dialog("How to read the interview resume · So lesen Sie den Interview-Lebenslauf", width="large")
def _tour_dialog(code: str) -> None:
    _tour_body(code, st.rerun)


# ------------------------------------------------------------------ pages

_CFG = None          # the app's settings reader, for the email to the researcher


def render_review(store, param: str, base_url: str | None = None, cfg=None) -> None:
    global _CFG
    _CFG = cfg
    st.markdown(CSS, unsafe_allow_html=True)
    param = (param or "").strip()
    if param.startswith("inv-"):
        _invite_page(store, param, base_url)
        return
    m = re.fullmatch(r"(R\d{2,3})\.([A-Za-z0-9_-]{6,})", param)
    rec = store.get_recruiter(m.group(1)) if m else None
    if not rec or not hmac.compare_digest(str(rec["key"]), m.group(2)):
        _topbar()
        st.error("This link is not valid. Please use the personal link or the invite link you received.")
        return
    _recruiter_page(store, rec, base_url)


def _invite_page(store, token: str, base_url) -> None:
    _topbar()
    problem = store.invite_problem(store.get_invite(token))
    if problem:
        msg = {"used": ("This invite link has already been used. If it was you, please continue with your personal "
                        "link.", "Dieser Einladungslink wurde bereits verwendet. Falls Sie das waren, nutzen Sie bitte "
                        "Ihren persönlichen Link."),
               "expired": ("This invite link has expired. Please ask the person who sent it for a new one.",
                           "Dieser Einladungslink ist abgelaufen. Bitte fragen Sie nach einem neuen."),
               "revoked": ("This invite link is no longer valid.", "Dieser Einladungslink ist nicht mehr gültig."),
               "unknown": ("This link is not valid.", "Dieser Link ist nicht gültig.")}[problem]
        st.error(A(*msg))
        return
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), CONSENT_FILE), encoding="utf-8") as f:
        text = f.read()
    with st.container(key="rv_narrow"):
        html("<div class='rv-hero'><div class='big'>👋</div><h2>Welcome, and thank you for helping!</h2>"
             "<span class='rv-de'>Willkommen, und danke für Ihre Hilfe!</span></div>")
        with st.container(border=True):
            html("<span class='rv-group'>What to expect · Was Sie erwartet</span>")
            md("- **About 25–35 minutes** for 3 candidates (about 8 minutes each); more only if you want.\n"
               "- For each candidate: **two documents**, a few **click questions** on each, one comparison.\n"
               "- A **one-minute guide** first. Your answers are saved after every step.",
               "Etwa 25–35 Minuten für 3 Personen (je etwa 8 Minuten), mehr nur wenn Sie möchten. Pro Person zwei "
               "Dokumente, je ein paar Klickfragen, ein Vergleich. Vorher eine einminütige Anleitung. Ihre Antworten "
               "werden nach jedem Schritt gespeichert.")
        html("<span class='rv-group'>Participant information · Teilnehmerinformation</span>")
        with st.container(key="rv_consenttext"):
            st.markdown(text)
        st.write("")
        with st.form("rv_consent", border=True):
            html("<span class='rv-group'>Your consent · Ihre Einwilligung</span>")
            md("Statements 1 to 4 are required, statement 5 is optional.",
               "Aussagen 1 bis 4 sind erforderlich, Aussage 5 ist freiwillig.")
            ticks = []
            for i, (en, de) in enumerate(CONSENT_STATEMENTS, 1):
                ticks.append(st.checkbox(f"{i}. {en}", key=f"rv_c{i}"))
                html(f"<div class='rv-de' style='margin:-6px 0 6px 28px'>{de}</div>")
            ok = st.form_submit_button("Agree and start · Zustimmen und starten", type="primary", width="stretch")
    if not ok:
        return
    if not all(ticks[:4]):
        st.error(A("Please tick statements 1 to 4 to take part.", "Bitte kreuzen Sie die Aussagen 1 bis 4 an."))
        return
    m = re.search(r"Version\s+(\d+(?:\.\d+)*)", text)
    consent = {"version": m.group(1) if m else "unknown", "statements": [i for i, t in enumerate(ticks, 1) if t],
               "at": datetime.now(timezone.utc).isoformat()}
    try:
        rec = store.redeem_invite(token, consent)
    except InviteError:
        st.error(A("This invite link has just been used.", "Dieser Einladungslink wurde gerade verwendet."))
        return
    st.session_state["rv_new"] = rec["code"]
    st.query_params["review"] = f"{rec['code']}.{rec['key']}"
    st.rerun()


def _recruiter_page(store, rec: dict, base_url) -> None:
    code = rec["code"]
    new = st.session_state.get("rv_new") == code
    _topbar(rec, base_url, open_link=new and not rec["guide_done"])

    if not rec["guide_done"]:
        _scroll_top_on_change("guide")
        md("**Before you start: a one-minute guide to the interview resume.** The example is fictional.",
           "**Vor dem Start: eine einminütige Anleitung zum Interview-Lebenslauf.** Das Beispiel ist fiktiv.")

        _tour_fragment(store, code)
        return

    stage_key = f"rv_{code}_stage"
    open_r = [r for r in store.list_reviews(recruiter=code) if r["status"] == "in_progress"]
    if open_r:
        _rate(store, rec, open_r[0])
        return
    stage = st.session_state.get(stage_key)
    if stage is None and rec["experience"] is None and completed(store, code) >= MIN_CANDIDATES:
        stage = "between"                       # e.g. reloaded after the 3rd candidate: ask the two questions first
    if stage == "between":
        _between(store, rec, base_url)
        return
    if stage == "thanks":
        _thanks(store, rec, base_url)
        return
    nxt = pick_next(store, code)
    if nxt is None:
        if completed(store, code) == 0:
            with st.container(key="rv_narrow"):
                st.info(A("There are no candidates available for you right now. Please try again later with your "
                          "personal link.",
                          "Derzeit sind keine Personen für Sie verfügbar. Bitte versuchen Sie es später mit Ihrem "
                          "persönlichen Link erneut."), icon=":material/hourglass_empty:")
        else:
            _thanks(store, rec, base_url, all_done=True)
        return
    store.create_review(code, **nxt)
    st.rerun()


STEP_LABELS = ["Document A · Dokument A", "Document B · Dokument B", "Comparison · Vergleich"]


def _header(done: int, idx: int) -> None:
    left, right = st.columns([3, 2], vertical_alignment="center")
    with left:
        md(f"**Candidate {done + 1}** · step {idx + 1} of 3", f"Person {done + 1} · Schritt {idx + 1} von 3")
    with right:
        html(f"<div style='text-align:right'>{_dots(done)}</div>")
    html(_chips(STEP_LABELS, idx))
    st.write("")


def _collect(rk: str, keys: list[str]) -> dict:
    return {k: st.session_state[f"{rk}_{k}"] for k in keys if st.session_state.get(f"{rk}_{k}") not in (None, "")}


def _save_doc(store, rv: dict, rk: str, screen: tuple[str, str]) -> None:
    """Callback: when all 10 answers of a document are given, save and move on."""
    keys = doc_keys(screen[1])
    ans = _collect(rk, keys)
    if len(ans) < len(keys):
        return
    t0 = st.session_state.pop(f"{rk}_t0", None)
    store.save_review_screen(rv["id"], screen[0], ans, round(time.time() - t0, 1) if t0 else None)
    st.session_state[f"{rk}_idx"] = 1 if screen[0] == "A" else 2


def _doc_pane(store, rv, doc: str, key: str) -> None:
    pdf = (store.get_profile(rv["profile_id"]) or {}).get("pdf") if doc == "profile" else (
        store.get_cv(rv["session_id"]) or {}).get("pdf")
    ident = f"profile-{rv['profile_id']}" if doc == "profile" else f"cv-{rv['session_id']}"
    with st.container(key=key):
        for png in _pages(doc, ident, pdf):
            st.image(png, width="stretch")


def _rate(store, rec: dict, rv: dict) -> None:
    code = rec["code"]
    rk = f"rv_{code}_{rv['id']}"
    sc = screens(rv["doc_order"])
    done_screens = set((rv["screens"] or {}).keys())
    if f"{rk}_idx" not in st.session_state:
        st.session_state[f"{rk}_idx"] = next((i for i, s in enumerate(sc) if s[0] not in done_screens), 2)
    idx = st.session_state[f"{rk}_idx"]
    st.session_state.setdefault(f"{rk}_t0", time.time())
    screen = sc[idx]
    for k, v in (rv["answers"] or {}).items():          # resume / Back: refill saved answers
        st.session_state.setdefault(f"{rk}_{k}", v)
    done = completed(store, code)
    _scroll_top_on_change(f"rate-{rv['id']}-{idx}")
    _header(done, idx)

    if screen[0] == "compare":
        _, mid, _ = st.columns([1, 4, 1])
        with mid, st.container(border=True):
            _compare_card(store, rec["code"], rv, rk, sc)
        md("Both documents for reference:", "Beide Dokumente zum Nachsehen:")
        a, b = st.columns(2, gap="medium")
        for col, (label, doc) in zip((a, b), (sc[0], sc[1])):
            with col:
                html(f"<span class='rv-doclabel'>Document {label} · Dokument {label}</span>")
                _doc_pane(store, rv, doc, f"docpanecmp{label}")
        return

    doc = screen[1]
    left, right = st.columns([3, 2], gap="large")
    with left:
        a, b = st.columns([1, 2], vertical_alignment="center")
        a.markdown(f"<span class='rv-doclabel'>Document {screen[0]} · Dokument {screen[0]}</span>",
                   unsafe_allow_html=True)
        if doc == "profile" and b.button("How to read this resume · So lesen Sie ihn", key=f"{rk}_howto",
                                         type="tertiary", icon=":material/help:"):
            _tour_dialog(code)
        _doc_pane(store, rv, doc, f"docpane{screen[0]}")
    with right, st.container(key="rv_q"):
        _doc_questions(store, rv, rk, screen, idx, screen[0] in done_screens)


def _leave(rk: str, screen_id: str) -> None:
    """Forget the answers a screen had when it was opened (so re-opening it starts a fresh comparison)."""
    st.session_state.pop(f"{rk}_entry_{screen_id}", None)


@st.fragment
def _doc_questions(store, rv: dict, rk: str, screen: tuple[str, str], idx: int, was_done: bool) -> None:
    """The 10 questions for one document. A click re-runs only this panel and touches no database; the answers
    are saved once, when the last one is given, and then the page moves on."""
    doc = screen[1]
    keys = doc_keys(doc)
    entry = f"{rk}_entry_{screen[0]}"
    if entry not in st.session_state:
        st.session_state[entry] = tuple(st.session_state.get(f"{rk}_{k}") for k in keys)
    n_done = len(_collect(rk, keys))
    md(f"**Rate Document {screen[0]} on its own** <span class='rv-count'>· {n_done} / {len(keys)}</span>",
       f"Bewerten Sie Dokument {screen[0]} für sich allein. Nach der letzten Antwort geht es automatisch weiter.")
    st.progress(n_done / len(keys))
    titles = [("Using this document", "Nutzen des Dokuments"),
              ("What it tells you", "Was es Ihnen sagt"),
              ("Reliability and distinctiveness", "Verlässlichkeit und Unterscheidbarkeit")]
    for (scale, lead, its), (t_en, t_de) in zip(GROUPS, titles):
        with st.container(border=True):
            html(f"<span class='rv-group'>{t_en}</span> <span class='rv-de'>· {t_de}</span>")
            if lead:
                md(f"**{lead[0]}**", lead[1])
            for iid, en, de in its:
                html(f"<p class='rv-q'>{en}<br><span class='rv-de'>{de}</span></p>")
                st.segmented_control(en, [1, 2, 3, 4, 5, 6, 7], key=f"{rk}_{doc}_{iid}",
                                     label_visibility="collapsed", width="stretch")
            lo, hi = scale[0].split(" · ")
            lo_de, hi_de = scale[1].split(" · ")
            html(f"<div class='rv-anchors'><span>{lo} <span class='rv-de'>({lo_de[4:]})</span></span>"
                 f"<span>{hi} <span class='rv-de'>({hi_de[4:]})</span></span></div>")
    current = tuple(st.session_state.get(f"{rk}_{k}") for k in keys)
    if None not in current and current != st.session_state[entry]:
        _save_doc(store, rv, rk, screen)                 # the only database write on this screen
        _leave(rk, screen[0])
        st.rerun()
    nav = st.columns(2)
    if idx > 0 and nav[0].button("← Back · Zurück", key=f"{rk}_back{idx}", width="stretch"):
        _leave(rk, screen[0])
        st.session_state[f"{rk}_idx"] = idx - 1
        st.rerun()
    if was_done and nav[1].button("Continue · Weiter →", key=f"{rk}_fwd{idx}", type="primary", width="stretch"):
        _save_doc(store, rv, rk, screen)
        _leave(rk, screen[0])
        st.rerun()
    if st.button("I recognise this person – skip · Ich erkenne diese Person – überspringen", key=f"{rk}_skip",
                 type="tertiary", icon=":material/visibility_off:"):
        store.skip_review(rv["id"])
        st.toast("Skipped. Here is the next candidate. · Übersprungen. Hier ist die nächste Person.")
        st.rerun()


@st.fragment
def _compare_card(store, code: str, rv: dict, rk: str, sc) -> None:
    html("<span class='rv-group'>Last question for this candidate</span> "
         "<span class='rv-de'>· Letzte Frage zu dieser Person</span>")
    html(f"<p class='rv-q'>{PREF[0]}<br><span class='rv-de'>{PREF[1]}</span></p>")
    st.segmented_control(PREF[0], PREF_OPTIONS, key=f"{rk}_pref", label_visibility="collapsed", width="stretch")
    html(f"<p class='rv-q'>{MISSING[0]}<br><span class='rv-de'>{MISSING[1]}</span></p>")
    st.text_area(MISSING[0], key=f"{rk}_missing", label_visibility="collapsed", height=68)
    nav = st.columns([1, 2])
    if nav[0].button("← Back · Zurück", key=f"{rk}_back2", width="stretch"):
        st.session_state[f"{rk}_idx"] = 1
        st.rerun()
    pref = st.session_state.get(f"{rk}_pref")
    if nav[1].button("Finish this candidate · Person abschließen", key=f"{rk}_finish", type="primary",
                     disabled=pref is None, width="stretch"):
        first = sc[0][1]
        pref_doc = {"Document A": first, "Document B": sc[1][1]}.get(pref, "equal")
        ans = {"pref": pref, "pref_doc": pref_doc}
        missing = (st.session_state.get(f"{rk}_missing") or "").strip()
        if missing:
            ans["missing"] = missing
        t0 = st.session_state.pop(f"{rk}_t0", None)
        store.save_review_screen(rv["id"], "compare", ans, round(time.time() - t0, 1) if t0 else None,
                                 complete=True)
        done = completed(store, code)
        st.session_state[f"rv_{code}_stage"] = "between" if done >= MIN_CANDIDATES else None
        st.toast(f"Candidate {done} done · Person {done} geschafft", icon=":material/check_circle:")
        st.rerun()
    if pref is None:
        st.caption("Choose an answer to finish. · Bitte eine Antwort wählen.")


def _between(store, rec: dict, base_url) -> None:
    code = rec["code"]
    done = completed(store, code)
    more = pick_next(store, code) is not None
    _scroll_top_on_change(f"between-{done}")
    with st.container(key="rv_narrow"), st.container(key="rv_between"):
        html(f"<div style='text-align:center'>{_dots(done)}</div>")
        st.write("")
        _between_card(store, code, rec["experience"] is None, done, more)


@st.fragment
def _between_card(store, code: str, ask: bool, done: int, more: bool) -> None:
    if ask:
        st.success(A(*MILESTONE), icon=":material/favorite:")
        with st.container(border=True):
            html("<span class='rv-group'>Two quick questions about you</span> "
                 "<span class='rv-de'>· Zwei kurze Fragen zu Ihnen</span>")
            html(f"<p class='rv-q'>{EXPERIENCE[0]}<br><span class='rv-de'>{EXPERIENCE[1]}</span></p>")
            exp = st.segmented_control(EXPERIENCE[0], EXPERIENCE_OPTIONS, key=f"rv_{code}_exp",
                                       label_visibility="collapsed", width="stretch")
            html(f"<p class='rv-q'>{DOMAIN[0]}<br><span class='rv-de'>{DOMAIN[1]}</span></p>")
            dom = st.text_input(DOMAIN[0], key=f"rv_{code}_dom", label_visibility="collapsed")
            html(f"<p class='rv-q'>{COMMENT[0]}<br><span class='rv-de'>{COMMENT[1]}</span></p>")
            com = st.text_area(COMMENT[0], key=f"rv_{code}_com", label_visibility="collapsed", height=80)

        def save():
            store.update_recruiter(code, experience=exp, domain=(dom or "").strip() or None,
                                   comment=(com or "").strip() or None)
    else:
        en, de = ROTATING[(done - MIN_CANDIDATES - 1) % len(ROTATING)]
        st.success(A(en.format(n=done), de.format(n=done)), icon=":material/favorite:")
        exp = True

        def save():
            pass
    if not more:
        st.info(A("You have rated every candidate available right now.",
                  "Sie haben alle derzeit verfügbaren Personen bewertet."))
    c = st.columns(2)
    if more and c[0].button("Rate another candidate (~8 min) · Noch eine Person", type="primary",
                            key=f"rv_{code}_another", disabled=exp is None, width="stretch",
                            icon=":material/arrow_forward:"):
        save()
        st.session_state[f"rv_{code}_stage"] = None
        st.rerun()
    if c[1].button("Finish · Beenden" if ask else "Stop here · Hier aufhören",
                   key=f"rv_{code}_stop", disabled=exp is None, width="stretch"):
        save()
        st.session_state[f"rv_{code}_stage"] = "thanks"
        st.rerun()
    if exp is None:
        st.caption("Please answer the experience question first. · Bitte zuerst die Frage zur Erfahrung beantworten.")


def _share(store, rec: dict, base_url) -> None:
    code = rec["code"]
    made = [i for i in store.list_invites() if i["created_by"] == code]
    left = MAX_SHARES - len(made)
    html("<span class='rv-group'>Invite a colleague</span> <span class='rv-de'>· Kollegen einladen</span>")
    md("Know another recruiter who could spare 30 minutes? Every additional recruiter makes the results more "
       "reliable.",
       "Kennen Sie weitere Recruiter, die 30 Minuten Zeit hätten? Jeder weitere Recruiter macht die Ergebnisse "
       "belastbarer.")
    sk = f"rv_{code}_shared"
    if st.session_state.get(sk):
        link = _invite_link(st.session_state[sk], base_url)
        md("**Your invite link** (single use, valid for 14 days). Copy it, or send it by email:",
           "Ihr Einladungslink (einmalig nutzbar, 14 Tage gültig). Kopieren oder per E-Mail senden:")
        st.code(link, language=None, wrap_lines=True)
        subject = "Could you help with a short recruiting study? / Kurze Recruiting-Studie"
        body = (f"Hi,\n\nI took part in a short study for a Bachelor's thesis at the University of Potsdam: you rate "
                f"a few candidates' CVs and an alternative resume format (about 30 minutes). Here is a personal invite "
                f"link (single use, valid 14 days):\n{link}\n\n---\n\nHallo,\n\nich habe an einer kurzen Studie für "
                f"eine Bachelorarbeit an der Universität Potsdam teilgenommen: Man bewertet die Lebensläufe einiger "
                f"Personen und ein alternatives Lebenslauf-Format (etwa 30 Minuten). Hier ist ein persönlicher "
                f"Einladungslink (einmalig nutzbar, 14 Tage gültig):\n{link}\n")
        st.link_button("Send by email · Per E-Mail senden", f"mailto:?subject={quote(subject)}&body={quote(body)}",
                       icon=":material/mail:", width="stretch")
    c1, c2 = st.columns([3, 2], vertical_alignment="center")
    if left > 0:
        label = ("New link · Neuer Link" if st.session_state.get(sk)
                 else "Create an invite link · Einladungslink erstellen")
        if c1.button(label, key=f"rv_{code}_share", icon=":material/person_add:", width="stretch",
                     type="secondary" if st.session_state.get(sk) else "primary"):
            st.session_state[sk] = store.create_invite(code)
            st.rerun()
        c2.markdown(f"<span class='rv-pill'>{left} of {MAX_SHARES} left · noch {left}</span>", unsafe_allow_html=True)
    else:
        st.caption("You have used all your invite links. Thank you! · Sie haben alle Einladungen genutzt. Danke!")


def _signup(store, code: str) -> None:
    sk = f"rv_{code}_signup"
    html("<span class='rv-group'>What's next</span> <span class='rv-de'>· Wie es weitergeht</span>")
    md(*PROTOTYPE)
    md(*NEXT_STEPS)
    if st.session_state.get(f"{sk}_done"):
        st.success(A("Thanks, you're on the list.", "Danke, Sie sind eingetragen."), icon=":material/check_circle:")
        return
    if not st.session_state.get(f"{sk}_open"):
        if st.button("Stay informed · Auf dem Laufenden bleiben", key=f"{sk}_btn", icon=":material/mail:",
                     type="primary", width="stretch"):
            st.session_state[f"{sk}_open"] = True
            st.rerun()
        return
    with st.form(f"{sk}_form", border=False):
        email = st.text_input("Email · E-Mail", key=f"{sk}_email", placeholder="name@company.com")
        agree = st.checkbox(SIGNUP_CONSENT[0], key=f"{sk}_agree")
        html(f"<div class='rv-de' style='margin:-6px 0 6px 28px'>{SIGNUP_CONSENT[1]}</div>")
        go = st.form_submit_button("Sign up · Eintragen", type="primary", width="stretch")
    if go:
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", (email or "").strip()):
            st.error(A("Please enter a valid email address.", "Bitte eine gültige E-Mail-Adresse eingeben."))
        elif not agree:
            st.error(A("Please tick the box to sign up.", "Bitte das Kästchen ankreuzen."))
        else:
            store.add_signup(email, SIGNUP_VERSION)
            st.session_state[f"{sk}_done"] = True
            st.rerun()


def _report_finish(store, rec: dict, done: int) -> None:
    """Emails the researcher this recruiter's ratings, once per finish (a later finish with more candidates sends an
    updated email). Runs in the background; a failure never reaches the recruiter."""
    key = f"rv_{rec['code']}_mailed"
    if not _CFG or done == 0 or st.session_state.get(key) == done:
        return
    st.session_state[key] = done
    try:
        import notify
        s = notify.settings(_CFG)
        if not s:
            return
        import admin
        table = admin.reviews_table(store)
        csv_text = table[table["recruiter"] == rec["code"]].to_csv(index=False) if len(table) else ""
        fresh = store.get_recruiter(rec["code"]) or rec
        notify.send_in_background(s, notify.recruiter_finished_message(
            s, fresh, store.list_reviews(recruiter=rec["code"]), csv_text))
    except Exception:
        import logging
        logging.getLogger("interview").exception("Finish email could not be prepared")


def _thanks(store, rec: dict, base_url, all_done: bool = False) -> None:
    code = rec["code"]
    done = completed(store, code)
    _report_finish(store, rec, done)
    _scroll_top_on_change("thanks")
    with st.container(key="rv_narrow"), st.container(key="rv_end"):
        html("<div class='rv-hero'><div class='big'>💚</div>"
             f"<h2>{THANKS[0]}</h2><span class='rv-de'>{THANKS[1]}</span><br><br>"
             f"<span class='rv-pill'>You rated {done} candidate{'s' if done != 1 else ''} · "
             f"Sie haben {done} Person{'en' if done != 1 else ''} bewertet</span></div>")
        if all_done:
            st.info(A("You have rated every candidate available right now.",
                      "Sie haben alle derzeit verfügbaren Personen bewertet."))
        st.write("")
        c1, c2 = st.columns(2, gap="medium")
        with c1, st.container(border=True, height="stretch"):
            _signup(store, code)
        with c2, st.container(border=True, height="stretch"):
            _share(store, rec, base_url)
        if not all_done and pick_next(store, code) is not None:
            st.write("")
            _, mid, _ = st.columns([1, 2, 1])
            if mid.button("Changed your mind? Rate another candidate · Doch noch eine Person bewerten",
                          key=f"rv_{code}_again", type="tertiary", width="stretch"):
                st.session_state[f"rv_{code}_stage"] = None
                st.rerun()
