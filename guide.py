"""Interactive reading guide for recruiters: a FICTIONAL sample resume, rendered with the real resume template, with
one area highlighted per step. No real candidate's data is used (confidentiality, and so that no recruiter studies
a resume they might later rate).
"""

from __future__ import annotations

import io

import pymupdf
from PIL import Image, ImageDraw

import profile_gen as pg

R1 = {"display": "Project coordinator, Nordhafen Logistics", "title": "Project coordinator", "category": "job",
      "type": "job", "period": "since 03/2023", "purpose": "Keep delivery projects on time and on budget",
      "tools_stated": ["MS Project", "Excel", "SAP"]}
R2 = {"display": "Workshop assistant, Radwerk bike repair", "title": "Workshop assistant", "category": "job",
      "type": "job", "period": "2020 – 2023", "purpose": "Repair bikes and advise customers",
      "tools_stated": ["repair tools", "cash register"]}
R3 = {"display": "Youth football coach, SV Lindenau", "title": "Youth football coach", "category": "volunteering",
      "type": "non-work", "period": "since 2021", "purpose": "Train the under-12 team twice a week",
      "tools_stated": []}
R4 = {"display": "Climbing", "title": "Climbing", "category": "hobby", "type": "non-work", "period": "",
      "purpose": "Stay fit and switch off", "tools_stated": []}

ENTRIES = [
    dict(num=1, role=R1["display"], dimensions=["ability"], labels=["Organising & Executing"],
         tags=["Under time pressure"], tools=["MS Project"],
         headline="Re-planned a delayed delivery so the client still received it on time",
         situation="A supplier delay put a client delivery three weeks behind schedule.",
         result="The delivery arrived on the original date; the client renewed the contract."),
    dict(num=2, role=R1["display"], dimensions=["ability"], labels=["Analysing & Interpreting"],
         tags=["Self-initiated"], tools=["Excel"],
         headline="Built a cost tracker that showed which routes lost money",
         situation="Nobody could say which delivery routes were unprofitable.",
         result="Two loss-making routes were merged; costs fell in the next quarter."),
    dict(num=3, role=R1["display"], dimensions=["integrity"], labels=["Interacting & Presenting"],
         tags=["Involved conflict or disagreement"], tools=[],
         headline="Told the manager about a budget overrun before the client review",
         situation="A cost error in the project budget had not been reported to the client.",
         result="The manager corrected the figures before the review; the client accepted the new budget."),
    dict(num=4, role=R2["display"], dimensions=["benevolence"], labels=["Supporting & Cooperating"],
         tags=["Self-initiated"], tools=[],
         headline="Trained a new colleague on repairs during own breaks",
         situation="A new colleague struggled with gear repairs during the busy season.",
         result="The colleague handled gear repairs alone after two weeks."),
    dict(num=5, role=R3["display"], dimensions=["benevolence"], labels=["Supporting & Cooperating"],
         tags=["Self-initiated"], tools=[],
         headline="Organised lifts to training for players without transport",
         situation="Three players kept missing training because their parents worked late.",
         result="All three attended regularly for the rest of the season."),
    dict(num=6, role=R3["display"], dimensions=["integrity"], labels=["Leading & Deciding"],
         tags=["Involved conflict or disagreement"], tools=[],
         headline="Kept equal playing time despite pressure to field the strongest team",
         situation="Before a cup match, parents asked for only the strongest players to play.",
         result="Every player played at least one half; the team lost but all players stayed."),
    dict(num=7, role=R4["display"], dimensions=["ability"], labels=["Adapting & Coping"],
         tags=[], tools=[],
         headline="Prepared step by step for a first outdoor climbing route",
         situation="The climber wanted to move from indoor walls to a first outdoor route.",
         result="Completed the route after four practice sessions."),
]


def sample_profile() -> dict:
    roles = [R1, R2, R3, R4]
    rows = []
    for r in roles:
        es = [e for e in ENTRIES if e["role"] == r["display"]]
        rows.append({"situations": [e["num"] for e in es],
                     "counts": {d: sum(d in e["dimensions"] for e in es) for d in pg.DIMENSIONS}})
    roles_with = {d: sum(1 for row in rows if row["counts"][d]) for d in pg.DIMENSIONS}
    stats = {}
    for e in ENTRIES:
        ri = [r["display"] for r in roles].index(e["role"])
        for lab in e["labels"]:
            s = stats.setdefault(lab, {"label": lab, "situations": 0, "role_indices": []})
            s["situations"] += 1
            if ri not in s["role_indices"]:
                s["role_indices"].append(ri)
    label_stats = sorted(({**s, "roles": len(s["role_indices"])} for s in stats.values()),
                         key=lambda s: (-s["roles"], -s["situations"], s["label"]))
    return {
        "candidate_id": "SAMPLE", "roles": roles, "entries": ENTRIES, "label_stats": label_stats,
        "overview": {"n_situations": len(ENTRIES), "n_roles": len(roles), "rows": rows, "roles_with": roles_with},
        "categories": [
            {"statement": "Steps in when someone else is struggling", "situations": 2, "roles": 2,
             "summary": "Trained a new colleague during own breaks [4] and organised lifts for players without "
                        "transport [5]."},
            {"statement": "Raises problems openly, even when it causes friction", "situations": 2, "roles": 2,
             "summary": "Reported a budget overrun before the client review [3] and kept equal playing time against "
                        "parents' wishes [6]."},
        ],
    }


_PDF: bytes | None = None


def sample_pdf() -> bytes:
    global _PDF
    if _PDF is None:
        _PDF = pg.render_pdf(sample_profile())
    return _PDF


# ------------------------------------------------------------------ steps

STEPS = [
    {"title": ("The interview resume", "Der Interview-Lebenslauf"),
     "text": ("For every candidate you see two documents: their own CV, and this resume. It was written from a "
              "structured interview in which the candidate described real situations from their jobs, volunteering "
              "and hobbies. This example is fictional.",
              "Zu jeder Person sehen Sie zwei Dokumente: ihren eigenen Lebenslauf und diesen Lebenslauf. Er entstand "
              "aus einem strukturierten Interview, in dem die Person echte Situationen aus Jobs, Ehrenamt und Hobbys "
              "geschildert hat. Dieses Beispiel ist fiktiv.")},
    {"title": ("Rows: the candidate's roles", "Zeilen: die Rollen der Person"),
     "text": ("Each row is one role the candidate entered, with its type (job, volunteering, hobby), period, purpose "
              "and the tools they named.",
              "Jede Zeile ist eine Rolle, die die Person angegeben hat, mit Art (Job, Ehrenamt, Hobby), Zeitraum, "
              "Zweck und den genannten Werkzeugen.")},
    {"title": ("Columns: three themes", "Spalten: drei Themen"),
     "text": ("Every situation was asked for under one of three themes:\n\n"
              "- **Doing the work**: getting a task done, with a result you could observe.\n"
              "- **Standing by standards**: acting on a rule, a commitment or a conflict between obligations.\n"
              "- **Helping others**: acting for someone else, at some cost to oneself.",
              "Jede Situation wurde zu einem von drei Themen erfragt: **Doing the work** (Die Arbeit erledigen): eine "
              "Aufgabe mit sichtbarem Ergebnis. **Standing by standards** (Zu Standards stehen): Handeln nach einer "
              "Regel, einer Zusage oder bei widersprüchlichen Pflichten. **Helping others** (Anderen helfen): Handeln "
              "für andere, mit eigenem Aufwand.")},
    {"title": ("The numbers", "Die Zahlen"),
     "text": ("Each number counts **how many situations** the candidate described for that role and theme. Here: two "
              "situations as project coordinator that show *Doing the work*. A dash means none. Darker blue means "
              "more situations. The number is a count, not a score.",
              "Jede Zahl zählt, **wie viele Situationen** die Person für diese Rolle und dieses Thema geschildert hat. "
              "Hier: zwei Situationen als Projektkoordinator zum Thema *Doing the work*. Ein Strich bedeutet keine. "
              "Dunkleres Blau bedeutet mehr Situationen. Die Zahl ist eine Anzahl, keine Bewertung.")},
    {"title": ("Does a theme recur?", "Kommt ein Thema wiederholt vor?"),
     "text": ("The last row shows in how many roles each theme appears. *2 of 4* means: situations for this theme came "
              "from 2 of the candidate's 4 roles. A theme that appears across several roles is more likely to describe "
              "how the person usually acts, not a one-off.",
              "Die letzte Zeile zeigt, in wie vielen Rollen ein Thema vorkommt. *2 of 4* heißt: Situationen zu diesem "
              "Thema stammen aus 2 der 4 Rollen. Ein Thema über mehrere Rollen hinweg beschreibt eher, wie die Person "
              "üblicherweise handelt, als einen Einzelfall.")},
    {"title": ("Kinds of behaviour", "Verhaltensarten"),
     "text": ("Each situation also gets up to two labels for the kind of behaviour it shows, e.g. *Supporting & "
              "Cooperating*. The dots count situations; blue means the label appears in more than one role. A missing "
              "label only means no described situation showed it, not that the person lacks it.",
              "Jede Situation erhält außerdem bis zu zwei Labels für die Art des Verhaltens, z. B. *Supporting & "
              "Cooperating*. Die Punkte zählen Situationen; Blau heißt: in mehr als einer Rolle. Fehlt ein Label, "
              "wurde es in keiner Situation beschrieben; das heißt nicht, dass es der Person fehlt.")},
    {"title": ("Summary: recurring behaviour", "Zusammenfassung: wiederkehrendes Verhalten"),
     "text": ("Short statements of what the candidate did in situations from at least two roles. Numbers in brackets, "
              "like [3], point to the situations on page 2.",
              "Kurze Aussagen darüber, was die Person in Situationen aus mindestens zwei Rollen getan hat. Zahlen in "
              "Klammern wie [3] verweisen auf die Situationen auf Seite 2.")},
    {"title": ("Page 2: the situations", "Seite 2: die Situationen"),
     "text": ("Every situation, numbered, with role, theme and labels, then what the situation was and what resulted. "
              "Everything restates the candidate's own answers and was checked against them. The resume contains no "
              "ratings of the person.",
              "Jede Situation, nummeriert, mit Rolle, Thema und Labels, dann die Situation und das Ergebnis. Alles "
              "gibt die eigenen Antworten der Person wieder und wurde mit ihnen abgeglichen. Der Lebenslauf enthält "
              "keine Bewertung der Person.")},
]


def _find(page, text: str):
    hits = page.search_for(text)
    return hits[0] if hits else None


def highlight_rects() -> list[tuple[int, pymupdf.Rect]]:
    """(page index, rectangle in PDF points) per step, found from the template's own headings and cells."""
    doc = pymupdf.open(stream=sample_pdf(), filetype="pdf")
    p1, p2 = doc[0], doc[1]
    w = p1.rect.width
    x0, x1 = 38, w - 38
    title = _find(p1, "Behavioural resume")
    lede = _find(p1, "restated from a structured interview")
    grid_top = _find(p1, "Where behaviour was shown").y0
    dw, sbs, ho = _find(p1, "Doing the work"), _find(p1, "Standing by standards"), _find(p1, "Helping others")
    role1 = _find(p1, R1["display"]).y0
    role2 = _find(p1, R2["display"]).y0
    bottom = _find(p1, "Roles with at least one situation")
    labels_top = _find(p1, "Kinds of behaviour shown").y0
    summary_top = _find(p1, "Summary").y0
    footer = _find(p1, "page 1 of").y0
    summary_bot = max(b[3] for b in p1.get_text("blocks") if summary_top - 1 <= b[1] < footer - 1)
    s1, s2 = _find(p2, "[1]"), _find(p2, "[2]")
    rects = [
        (0, pymupdf.Rect(x0, title.y0 - 6, x1, lede.y1 + 6)),                       # the document
        (0, pymupdf.Rect(x0, role1 - 4, dw.x0 - 14, bottom.y0 - 3)),                 # rows: roles
        (0, pymupdf.Rect(dw.x0 - 12, dw.y0 - 5, x1, bottom.y0 - 3)),                 # columns: themes
        (0, pymupdf.Rect(dw.x0 - 10, role1 - 4, sbs.x0 - 14, role2 - 3)),            # one number
        (0, pymupdf.Rect(x0, bottom.y0 - 5, x1, bottom.y1 + 5)),                     # recurrence row
        (0, pymupdf.Rect(x0, labels_top - 4, x1, summary_top - 4)),                  # labels
        (0, pymupdf.Rect(x0, summary_top - 4, x1, summary_bot + 6)),                 # summary
        (1, pymupdf.Rect(x0, s1.y0 - 4, x1, s2.y0 - 3)),                             # situations
    ]
    doc.close()
    return rects


DPI = 110


def step_image(k: int) -> bytes:
    """PNG of the page for step k with everything outside the highlighted area dimmed and the area framed."""
    page_no, rect = highlight_rects()[k]
    doc = pymupdf.open(stream=sample_pdf(), filetype="pdf")
    png = doc[page_no].get_pixmap(dpi=DPI).tobytes("png")
    doc.close()
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    s = DPI / 72
    box = tuple(int(v * s) for v in (rect.x0, rect.y0, rect.x1, rect.y1))
    shade = Image.new("RGBA", img.size, (20, 30, 40, 120))
    shade.paste((0, 0, 0, 0), box)
    out = Image.alpha_composite(img, shade)
    ImageDraw.Draw(out).rounded_rectangle(box, radius=10, outline=(14, 107, 107, 255), width=5)
    # crop to the area around the highlight so it is always visible without scrolling
    top = max(0, box[1] - int(150 * s))
    bot = min(out.height, max(box[3] + int(170 * s), top + int(420 * s)))
    out = out.crop((0, top, out.width, bot))
    buf = io.BytesIO()
    out.convert("RGB").save(buf, "PNG")
    return buf.getvalue()
