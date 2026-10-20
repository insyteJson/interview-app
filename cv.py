"""CV anonymisation, local and without any AI service.

The original upload is processed in memory only and never stored. The result keeps the original layout; names,
contact details, links and images are blacked out (true PDF redaction: the text underneath is removed, not just
covered). A leftover check re-reads the result, and the participant reviews every page before submitting.
This reduces identifying detail but cannot guarantee it: the researcher still checks every CV (Appendix B, B.3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pymupdf

MAX_BYTES = 8 * 1024 * 1024
MAX_PAGES = 6
MIN_TEXT_CHARS = 80

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL = re.compile(r"(?:https?://|www\.)\S+|\b(?:linkedin|xing|github|gitlab|twitter|instagram)\.[a-z]{2,}\S*", re.I)
PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().\-/]{6,}\d(?!\w)")


class CvError(ValueError):
    """The upload cannot be processed; the message is safe to show the participant."""


@dataclass
class CvResult:
    pdf: bytes
    previews: list = field(default_factory=list)       # PNG bytes per page
    report: dict = field(default_factory=dict)


def _terms(name: str, others: str) -> list[str]:
    out: list[str] = []
    name = (name or "").strip()
    if name:
        out.append(name)
        out += [p for p in re.split(r"[\s,]+", name) if len(p) >= 2]   # first and last names separately
    out += [ln.strip() for ln in (others or "").splitlines() if len(ln.strip()) >= 2]
    seen, uniq = set(), []
    for t in out:
        if t.lower() not in seen:
            seen.add(t.lower())
            uniq.append(t)
    return sorted(uniq, key=len, reverse=True)


def _auto_matches(text: str) -> dict[str, list[str]]:
    emails = EMAIL.findall(text)
    urls = [u.rstrip(".,;)") for u in URL.findall(text)]
    phones = [p.strip() for p in PHONE.findall(text) if sum(ch.isdigit() for ch in p) >= 7]
    return {"email": emails, "link": urls, "phone": phones}


def redact_pdf(data: bytes, name: str, others: str = "") -> CvResult:
    if not data or len(data) > MAX_BYTES:
        raise CvError("The file is empty or larger than 8 MB.")
    if not data.startswith(b"%PDF"):
        raise CvError("Please upload a PDF file (in Word: File > Save as > PDF).")
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise CvError("This PDF could not be opened.") from exc
    if doc.needs_pass:
        raise CvError("This PDF is password-protected. Please upload an unprotected copy.")
    if doc.page_count > MAX_PAGES:
        raise CvError(f"Please upload a CV with at most {MAX_PAGES} pages.")
    if sum(len(p.get_text().strip()) for p in doc) < MIN_TEXT_CHARS:
        raise CvError("This PDF has no readable text (it may be a scan). Please send it to the researcher directly.")

    terms = _terms(name, others)
    found = {t: 0 for t in terms}
    auto = {"email": 0, "link": 0, "phone": 0}
    all_auto: set[str] = set()
    images = links = 0

    for page in doc:
        text = page.get_text()
        targets: list[tuple[str, str | None]] = [(t, t) for t in terms]
        for kind, vals in _auto_matches(text).items():
            for v in set(vals):
                targets.append((v, kind))
                all_auto.add(v)
        n_rects = 0
        for needle, kind in targets:
            for rect in page.search_for(needle):
                page.add_redact_annot(rect, fill=(0, 0, 0))
                n_rects += 1
                if kind in auto:
                    auto[kind] += 1
                else:
                    found[needle] += 1
        for info in page.get_images(full=True):
            for rect in page.get_image_rects(info[0]):
                page.add_redact_annot(rect, fill=(0, 0, 0))
                images += 1
                n_rects += 1
        for ln in page.get_links():
            page.delete_link(ln)
            links += 1
        if n_rects:
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS)

    doc.set_metadata({})
    try:
        doc.del_xml_metadata()
        doc.scrub(metadata=True, attached_files=True, embedded_files=True, javascript=True,
                  thumbnails=True, reset_fields=True, reset_responses=True)
    except Exception:  # scrub options differ between versions; metadata is already cleared above
        pass
    out = doc.tobytes(garbage=4, deflate=True, clean=True)
    doc.close()

    check = pymupdf.open(stream=out, filetype="pdf")
    remaining_text = "\n".join(p.get_text() for p in check).lower()
    leftovers = [t for t in list(terms) + sorted(all_auto) if t.lower() in remaining_text]
    previews = [p.get_pixmap(dpi=75).tobytes("png") for p in check]
    pages = check.page_count
    check.close()

    return CvResult(pdf=out, previews=previews, report={
        "pages": pages,
        "terms_not_found": [t for t, n in found.items() if n == 0],   # shown to the participant, never stored
        "blacked_out": {"email": auto["email"], "phone": auto["phone"], "link": auto["link"],
                        "own_terms": sum(found.values()), "images": images},
        "links_removed": links,
        "leftovers": leftovers,
    })


def storable_report(report: dict) -> dict:
    """What is saved with the CV: counts only, no names or terms."""
    return {"pages": report["pages"], "blacked_out": report["blacked_out"],
            "links_removed": report["links_removed"], "leftover_count": len(report["leftovers"]),
            "terms_without_match": len(report["terms_not_found"])}
