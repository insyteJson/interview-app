"""Role form: categories, the fields each one asks for, validation and display. No Streamlit, no model.

The participant enters the roles to be discussed in a short form before the first interview question.
Everything the interview and the resume need about a role is typed by the participant; nothing is inferred.

Category      engine type   asks for                                         period
Job           job           job title*, company*                             from* / to* (MM/YYYY or YYYY; "now")
Volunteering  non-work      role*, organisation*                             from / to, optional (both or none)
Hobby         non-work      what you do, as a role* (e.g. "marathon runner"), club or team (optional)   none

The engine type decides the stopping rules (Appendix C §9): jobs get the fallback prompt and the continuation
offer, non-work roles do not.
"""

from __future__ import annotations

import re
from datetime import date

CATEGORIES: dict[str, dict] = {
    "job": {
        "name": "Job", "plural": "Jobs", "type": "job",
        "hint": "paid work, including student jobs, internships and freelance work",
        "title": ("Job title", "Job title, e.g. software developer"), "org": ("Company", "Company", True),
        "period": "required",
    },
    "volunteering": {
        "name": "Volunteering", "plural": "Volunteering", "type": "non-work",
        "hint": "unpaid role in an organisation, club or initiative; dates optional",
        "title": ("Role", "Your role, e.g. youth coach"), "org": ("Organisation", "Organisation", True),
        "period": "optional",
    },
    "hobby": {
        "name": "Hobby", "plural": "Hobbies", "type": "non-work",
        "hint": "something you do regularly in your free time",
        "title": ("What you do", "What you do, e.g. marathon runner"),
        "org": ("Club or team", "Club or team (optional)", False),
        "period": "none",
    },
}
ORDER = ["job", "volunteering", "hobby"]
JOB_TYPES = ["Full-time", "Part-time", "Working student", "Internship", "Apprenticeship / dual study",
             "Freelance / self-employed", "Student job / mini-job"]
MAX_ROLES = 10
NOW_WORDS = {"now", "today", "present", "current", "currently", "ongoing", "heute", "jetzt", "aktuell"}
NO_COMPANY = {"self-employed", "self employed", "freelance", "freelancer", "selbstständig", "selbststaendig"}
_DATE = re.compile(r"^\s*(?:(\d{1,2})\s*[/.\-]\s*)?(\d{4})\s*$|^\s*(\d{4})\s*-\s*(\d{1,2})\s*$")


def parse_date(text: str, allow_now: bool = False):
    """'2021', '3/2021', '03.2021', '2021-03' -> (2021, 3 or None); 'now' -> 'now'; '' -> None. Raises ValueError."""
    t = (text or "").strip().lower()
    if not t:
        return None
    if t in NOW_WORDS:
        if allow_now:
            return "now"
        raise ValueError("a start date cannot be 'now'")
    m = _DATE.match(t)
    if not m:
        raise ValueError(f"'{text.strip()}' is not a date like 03/2021 or 2021")
    if m.group(2):
        month, year = (int(m.group(1)) if m.group(1) else None), int(m.group(2))
    else:
        year, month = int(m.group(3)), int(m.group(4))
    if month is not None and not 1 <= month <= 12:
        raise ValueError(f"'{text.strip()}' has no valid month")
    if not 1950 <= year <= date.today().year:
        raise ValueError(f"'{text.strip()}' is not a plausible year")
    if (year, month or 1) > (date.today().year, date.today().month):
        raise ValueError(f"'{text.strip()}' is in the future")
    return (year, month)


def fmt_date(d) -> str:
    if d == "now":
        return "now"
    year, month = d
    return f"{month:02d}/{year}" if month else str(year)


def fmt_period(start, end) -> str | None:
    if not start:
        return None
    if end == "now":
        return f"since {fmt_date(start)}"
    if end is None or fmt_date(start) == fmt_date(end):
        return fmt_date(start)
    return f"{fmt_date(start)} – {fmt_date(end)}"


def make_label(title: str, org: str | None) -> str:
    """What the fixed questions insert as {label}, e.g. 'working student at Test GmbH'."""
    if not org or org.lower() in NO_COMPANY or org.lower() in title.lower():
        return title
    return f"{title} at {org}"


def normalise(entries: list[dict]) -> tuple[list[dict], list[str]]:
    """Form rows -> (positions, errors). Each row: {cat, kind, title, org, start, end}; kind = job type (jobs only).
    Empty rows are ignored."""
    positions, errors, count = [], [], {c: 0 for c in CATEGORIES}
    for e in entries:
        cat = e.get("cat")
        spec = CATEGORIES.get(cat)
        if spec is None:
            continue
        title = " ".join((e.get("title") or "").split())
        org = " ".join((e.get("org") or "").split())
        start_t, end_t = (e.get("start") or "").strip(), (e.get("end") or "").strip()
        kind = (e.get("kind") or "").strip() if cat == "job" else ""
        if not (title or org or start_t or end_t):
            continue  # untouched row
        count[cat] += 1
        name = f"{spec['name']} {count[cat]}"
        problems = []
        if cat == "job" and kind not in JOB_TYPES:
            problems.append("choose the type of job")
        if not title:
            problems.append(f"add the {spec['title'][0].lower()}")
        if len(title) > 80 or len(org) > 80:
            problems.append("please shorten it to a few words")
        if spec["org"][2] and not org:
            problems.append(f"add the {spec['org'][0].lower()}" + (" (or 'self-employed')" if cat == "job" else ""))
        start = end = None
        if spec["period"] != "none":
            try:
                start = parse_date(start_t)
            except ValueError as exc:
                problems.append(f"start date: {exc}")
            try:
                end = parse_date(end_t, allow_now=True)
            except ValueError as exc:
                problems.append(f"end date: {exc}")
            if spec["period"] == "required":
                if not start_t:
                    problems.append("add when it started (e.g. 03/2021 or 2021)")
                if not end_t:
                    problems.append("add when it ended, or write 'now'")
            elif bool(start_t) != bool(end_t) and not problems:
                problems.append("add both dates (end can be 'now'), or leave both empty")
            if start and end and end != "now" and (end[0], end[1] or 12) < (start[0], start[1] or 1):
                problems.append("the end is before the start")
        if problems:
            errors.append(f"{name}: " + "; ".join(problems) + ".")
            continue
        positions.append({
            "item": f"{spec['name']}: {title}" + (f", {org}" if org else ""),
            "category": cat, "type": spec["type"], "title": title, "company": org or None,
            "employment": kind or None,
            "period": fmt_period(start, end),
            "period_from": fmt_date(start) if start else None, "period_to": fmt_date(end) if end else None,
            "label": make_label(title, org or None), "aim": None, "tools": [],
        })
    if not positions and not errors:
        errors.append("Please add at least one job, volunteering role or hobby.")
    if len(positions) > MAX_ROLES:
        errors.append(f"Please keep it to {MAX_ROLES} entries at most.")
    labels = [p["label"].lower() for p in positions]
    if len(set(labels)) < len(labels):
        errors.append("Two entries are identical. Please remove one or tell them apart (e.g. by company).")
    return positions, errors


def title_with_type(p: dict) -> str:
    """'Software developer (Working student)'; the type is left out if the title already says it."""
    kind = p.get("employment")
    if not kind or kind.split(" /")[0].lower() in p["title"].lower():
        return p["title"]
    return f"{p['title']} ({kind})"


def summary(positions: list[dict]) -> str:
    """The participant's own entries, shown as their chat message and stored as the turn's answer."""
    lines = []
    for p in positions:
        bits = [title_with_type(p)] + [x for x in (p.get("company"), p.get("period")) if x]
        lines.append(f"- **{CATEGORIES[p['category']]['name']}:** " + ", ".join(bits))
    return "\n".join(lines)


def parse_roles_block(text: str) -> list[dict]:
    """For replay files: 'job; Full-time; Software developer; Test GmbH; 03/2021; now' (job type only for jobs),
    'volunteering; Youth coach; FC Example; 2019; now', 'hobby; Marathon runner; Lauftreff'."""
    rows = []
    for line in text.splitlines():
        if not line.strip() or line.strip().upper().startswith("ROLES"):
            continue
        f = [x.strip() for x in line.split(";")] + [""] * 6
        cat = f[0].lower()
        if cat == "job":
            rows.append({"cat": cat, "kind": f[1], "title": f[2], "org": f[3], "start": f[4], "end": f[5]})
        else:
            rows.append({"cat": cat, "title": f[1], "org": f[2], "start": f[3], "end": f[4]})
    return rows
