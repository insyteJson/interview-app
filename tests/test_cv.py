import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymupdf  # noqa: E402
import pytest  # noqa: E402

import cv  # noqa: E402


def make_pdf(with_text=True):
    doc = pymupdf.open()
    page = doc.new_page()
    if with_text:
        y = 72
        for line in [
            "Jason Harris", "Product Owner", "jason.harris@example.com", "+49 151 2345 6789",
            "https://www.linkedin.com/in/jasonharris",
            "2021-2024 Acme Robotics GmbH, Heidelberg: led a team of five engineers and shipped the platform.",
            "Skills: Python, SQL, stakeholder management, agile delivery and product discovery workshops.",
        ]:
            page.insert_text((72, y), line, fontsize=11)
            y += 20
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
        pix.clear_with(200)
        page.insert_image(pymupdf.Rect(450, 40, 520, 110), pixmap=pix)
        page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 120, 300, 135),
                          "uri": "https://example.com/jason"})
    doc.set_metadata({"author": "Jason Harris", "title": "CV Jason Harris"})
    return doc.tobytes()


def test_redacts_text_removes_images_links_and_metadata():
    res = cv.redact_pdf(make_pdf(), "Jason Harris", "Acme Robotics GmbH\nHeidelberg")
    out = pymupdf.open(stream=res.pdf, filetype="pdf")
    text = "\n".join(p.get_text() for p in out).lower()
    for gone in ["jason", "harris", "example.com", "linkedin", "2345", "acme", "heidelberg"]:
        assert gone not in text, gone
    assert "led a team of five engineers" in text        # content stays
    assert out.page_count == 1 and len(res.previews) == 1
    assert res.report["blacked_out"]["images"] == 1
    assert res.report["leftovers"] == []
    assert not any(p.get_links() for p in out)
    assert not (out.metadata.get("author") or "") and "jason" not in (out.metadata.get("title") or "").lower()


def test_reports_terms_not_found_and_storable_report_has_no_names():
    res = cv.redact_pdf(make_pdf(), "Jason Harris", "Nonexistent Corp")
    assert "Nonexistent Corp" in res.report["terms_not_found"]
    stored = cv.storable_report(res.report)
    assert "Jason" not in str(stored) and "Nonexistent" not in str(stored)


@pytest.mark.parametrize("data, msg", [
    (b"", "empty"), (b"not a pdf at all", "PDF"), (make_pdf(with_text=False), "no readable text"),
])
def test_rejects_unusable_uploads(data, msg):
    with pytest.raises(cv.CvError) as e:
        cv.redact_pdf(data, "Jason Harris")
    assert msg.lower() in str(e.value).lower()


def _text(pdf):
    d = pymupdf.open(stream=pdf, filetype="pdf")
    t = "\n".join(p.get_text() for p in d)
    d.close()
    return t


def test_later_blackout_is_cumulative_and_reports_counts():
    first = cv.redact_pdf(make_pdf(), "Jason Harris").pdf
    assert "Acme Robotics" in _text(first)
    r1 = cv.redact_more(first, "Acme Robotics")
    assert r1.report["per_term"] == {"Acme Robotics": 1} and not r1.report["leftovers"]
    assert "Acme Robotics" not in _text(r1.pdf) and "Heidelberg" in _text(r1.pdf)
    r2 = cv.redact_more(r1.pdf, "Heidelberg\nNotInTheCV")
    t2 = _text(r2.pdf)
    assert "Heidelberg" not in t2 and "Acme Robotics" not in t2 and "Jason" not in t2   # earlier rounds stay
    assert r2.report["per_term"]["NotInTheCV"] == 0
    assert "Product Owner" in t2                                                          # the rest is untouched


def test_replace_cv_counts_rounds_and_asks_for_a_new_check(tmp_path):
    from storage import Store
    store = Store(f"sqlite:///{tmp_path}/c.db")
    first = cv.redact_pdf(make_pdf(), "Jason Harris")
    store.save_cv("P01", first.pdf, cv.storable_report(first.report))
    store.mark_cv_checked("P01", True)
    store.replace_cv_pdf("P01", cv.redact_more(first.pdf, "Acme Robotics").pdf, 1)
    store.replace_cv_pdf("P01", cv.redact_more(store.get_cv("P01")["pdf"], "Heidelberg").pdf, 1)
    row = store.get_cv("P01")
    assert row["checked"] == 0 and row["report"]["researcher_blackouts"]["rounds"] == 2
    assert row["report"]["pages"] == 1                                    # the upload report is kept
    assert "Acme" not in _text(row["pdf"]) and "Heidelberg" not in _text(row["pdf"])



def test_later_blackout_leaves_dates_alone():
    first = cv.redact_pdf(make_pdf(), "Jason Harris").pdf
    before = _text(first)
    out = cv.redact_more(first, "Acme Robotics").pdf
    after = _text(out)
    assert sum(ch.isdigit() for ch in after) == sum(ch.isdigit() for ch in before)   # no digits removed


def test_redact_more_finds_terms_behind_ligatures_and_line_breaks():
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_htmlbox(pymupdf.Rect(50, 50, 200, 300),
                        "<p>Stay fit in the office. Worked at Universitätsmedizin Göttingen for a while.</p>")
    data = doc.tobytes()
    res = cv.redact_more(data, "office\nUniversitätsmedizin Göttingen")
    assert res.report["per_term"]["office"] >= 1 and res.report["per_term"]["Universitätsmedizin Göttingen"] >= 1
    assert res.report["leftovers"] == []
    flat = cv._flat(" ".join(p.get_text(flags=cv.SEARCH_FLAGS) for p in pymupdf.open(stream=res.pdf, filetype="pdf")))
    assert "office" not in flat and "göttingen" not in flat and "stay fit" in flat
