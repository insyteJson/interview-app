import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import roles  # noqa: E402


def R(cat, title="", org="", start="", end=""):
    return {"cat": cat, "title": title, "org": org, "start": start, "end": end}


@pytest.mark.parametrize("text,expected", [("2021", (2021, None)), ("3/2021", (2021, 3)), ("03.2021", (2021, 3)),
                                           ("2021-03", (2021, 3)), (" 12 / 2020 ", (2020, 12)), ("", None)])
def test_parse_date_formats(text, expected):
    assert roles.parse_date(text) == expected


@pytest.mark.parametrize("text", ["13/2021", "march 2021", "1890", str(date.today().year + 1), "now"])
def test_parse_date_rejects(text):
    with pytest.raises(ValueError):
        roles.parse_date(text)


def test_now_only_allowed_as_end():
    assert roles.parse_date("Now", allow_now=True) == "now" and roles.parse_date("heute", allow_now=True) == "now"


def test_job_needs_title_company_and_both_dates():
    pos, err = roles.normalise([R("job", "Working student")])
    assert not pos and len(err) == 1
    for bit in ("add the company", "when it started", "when it ended"):
        assert bit in err[0]


def test_job_ok_label_period_and_type():
    pos, err = roles.normalise([R("job", "Working student", "Test GmbH", "03/2021", "08/2023"),
                                R("job", "Designer", "self-employed", "2019", "now")])
    assert not err
    a, b = pos
    assert a["label"] == "Working student at Test GmbH" and a["period"] == "03/2021 – 08/2023" and a["type"] == "job"
    assert b["label"] == "Designer" and b["period"] == "since 2019"           # no "at self-employed"


def test_end_before_start_is_refused():
    _, err = roles.normalise([R("job", "Cook", "Inn", "2022", "2021")])
    assert "end is before the start" in err[0]


def test_volunteering_period_optional_but_both_or_none():
    pos, err = roles.normalise([R("volunteering", "Youth coach", "FC Example")])
    assert not err and pos[0]["period"] is None and pos[0]["type"] == "non-work"
    _, err = roles.normalise([R("volunteering", "Youth coach", "FC Example", "2019")])
    assert "both dates" in err[0]
    _, err = roles.normalise([R("volunteering", "Youth coach")])
    assert "add the organisation" in err[0]


def test_hobby_needs_only_what_you_do_and_ignores_dates():
    pos, err = roles.normalise([R("hobby", "Formation dancer", "", "2015", "now"), R("hobby", "Runner", "Lauftreff")])
    assert not err
    assert pos[0]["period"] is None and pos[0]["label"] == "Formation dancer" and pos[0]["category"] == "hobby"
    assert pos[1]["label"] == "Runner at Lauftreff"


def test_empty_rows_ignored_but_one_entry_required_and_duplicates_refused():
    _, err = roles.normalise([R("job"), R("hobby")])
    assert err == ["Please add at least one job, volunteering role or hobby."]
    _, err = roles.normalise([R("hobby", "Runner"), R("hobby", "runner")])
    assert "identical" in err[0]


def test_summary_and_replay_block():
    rows = roles.parse_roles_block("ROLES\njob; IT team lead; a startup; 01/2024; now\nhobby; Dancer")
    pos, err = roles.normalise(rows)
    assert not err
    assert roles.summary(pos) == "- **Job:** IT team lead, a startup, since 01/2024\n- **Hobby:** Dancer"
