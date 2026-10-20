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
