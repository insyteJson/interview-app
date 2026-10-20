"""Researcher view, part 2: redaction pass, profile generation, operator check, PDF and packet download."""

from __future__ import annotations

import io
import json
import zipfile

import pymupdf
import streamlit as st

import profile_gen as pg


def _preview_pngs(pdf: bytes) -> list[bytes]:
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    out = [p.get_pixmap(dpi=80).tobytes("png") for p in doc]
    doc.close()
    return out


def render_profile_section(store, sid: str, state: dict, rows: list[dict], make_generator) -> None:
    st.subheader("Profile for recruiters")
    if state["status"] != "complete":
        st.caption("Available once the interview is complete.")
        return
    positions, incidents = state.get("positions") or [], state.get("incidents") or []
    if not incidents:
        st.info("This session has no accepted incidents, so there is nothing to build a profile from.")
        return

    # ---------------- step 1: redaction
    st.markdown("**Step 1 · Remove personal names** (mandatory before generation). Company names, titles, places "
                "and achievements stay; names of people are replaced, e.g. by their role.")
    red = store.get_redaction(sid)
    exchanges = {inc["id"]: pg.incident_exchanges(inc, rows) for inc in incidents}
    texts = [", ".join(p.get("tools") or []) for p in positions]
    texts += [e["answer"] for ex in exchanges.values() for e in ex]
    sugg = pg.suggest_identifiers(texts)
    with st.expander("Capitalised words in the answers that may be personal names (a reading aid, not a complete list)"):
        st.write(", ".join(f"{w} ({n}×)" for w, n in sugg) or "None found.")
    rules_text = st.text_area(
        "Replacement rules, one per line, `find => replacement` (a line without `=>` becomes [name removed]). "
        "Matching ignores case and whole words only.",
        value=red["rules"] if red else "", key=f"rules_{sid}", height=140,
        placeholder="Anna => a colleague\nMr Weber => the manager")
    rules = pg.parse_rules(rules_text)
    with st.expander("Everything the profile is built from, after replacement — read all of it", expanded=False):
        for p in positions:
            st.markdown(f"**{pg.apply_rules(pg.role_display(p), rules)}** ({p.get('period') or 'no period'}) · "
                        f"tools: {pg.apply_rules(', '.join(p.get('tools') or []), rules) or '—'}")
        for inc in incidents:
            st.markdown(f"**{inc['id']} · {inc['position_label']} · {inc['dimension']}**")
            for e in exchanges[inc["id"]]:
                st.markdown(f"> {pg.apply_rules(e['answer'], rules)}")
    saved_same = red is not None and red["rules"] == rules_text
    confirm = st.checkbox("I read all of the text above. No personal name remains.",
                          key=f"redok_{sid}")
    if st.button("Save redaction", disabled=not confirm, key=f"redsave_{sid}"):
        store.save_redaction(sid, rules_text)
        st.rerun()
    if red and saved_same:
        st.caption(f"Redaction saved {red['confirmed_at']:%d.%m.%Y %H:%M} UTC with {len(rules)} rule(s).")
    elif red:
        st.warning("The rules differ from the saved redaction. Save again before generating.")

    # ---------------- step 2: generation
    st.markdown("**Step 2 · Generate**")
    profs = store.list_profiles(sid)
    reason = st.text_input("Reason for this generation (logged)", key=f"reason_{sid}",
                           value="" if profs else "first generation")
    if st.button("Generate profile", type="primary", key=f"gen_{sid}",
                 disabled=not (saved_same and reason.strip())):
        gen = make_generator()
        if gen is None:
            st.error("ANTHROPIC_API_KEY is not configured.")
        else:
            bar = st.progress(0.0, text="Generating…")
            res = pg.generate_profile(state, rows, pg.parse_rules(red["rules"]), gen,
                                      progress=lambda k, n: bar.progress(k / n, text=f"Incident {k + 1} of {n}…"))
            pdf = pg.render_pdf(res["profile"]) if res["ok"] else None
            store.add_profile(sid, pg.PROFILE_VERSION, res["ok"], reason.strip(), res["profile"], res["log"], pdf)
            st.rerun()
    if not profs:
        return

    # ---------------- step 3: operator check
    latest = profs[0]
    log = latest["log"]
    attempts = sum(len(i["attempts"]) for i in log["incidents"])
    tok_in = sum(a.get("meta", {}).get("input_tokens", 0) for i in log["incidents"] for a in i["attempts"])
    tok_out = sum(a.get("meta", {}).get("output_tokens", 0) for i in log["incidents"] for a in i["attempts"])
    st.caption(f"{len(profs)} generation(s) logged · latest #{latest['id']} ({latest['reason']}) · "
               f"{attempts} model call(s) for {len(log['incidents'])} incidents · tokens {tok_in:,} in / {tok_out:,} out")
    if not latest["ok"]:
        st.error("The latest generation failed the checks for at least one incident after 3 attempts, so no PDF "
                 "was produced. Fix the cause (usually a missing redaction rule) and generate again.")
        for i in log["incidents"]:
            if not i["passed"]:
                st.markdown(f"**{i['id']}**")
                for a in i["attempts"]:
                    st.write(a.get("error") or a.get("fails"))
        return

    st.markdown("**Step 3 · Check every entry against the answers** (never edit by hand; regenerate instead)")
    prof = latest["profile"]
    for d in pg.DIMENSIONS:
        for x in prof["themes"][d]:
            with st.expander(f"{x['id']} · {pg.HEADINGS[d]} · {x['headline']}"):
                c1, c2 = st.columns(2)
                c1.markdown("**Answers (after redaction)**")
                for e in x["source"]:
                    c1.markdown(f"> {e['answer']}")
                c2.markdown(f"**{x['headline']}**  \n{x['role']}")
                c2.markdown(f"Situation: {x['situation']}  \nResult: {x['result']}")
                for ev in x.get("label_evidence", []):
                    c2.caption(f"Label {ev['name']}: “{ev['quote']}”")
                for ev in x.get("tag_evidence", []):
                    c2.caption(f"Tag {ev['name']}: “{ev['quote']}”")
                if x.get("tools"):
                    c2.caption("Tools: " + ", ".join(x["tools"]))
                for w in x.get("warnings", []):
                    c2.warning(w)
    approved = st.checkbox("I checked every entry against the answers. Nothing is added, every label and tag meets "
                           "its coding rule, no personal name appears.",
                           value=bool(latest["approved"]), key=f"appr_{sid}_{latest['id']}")
    if approved != bool(latest["approved"]):
        store.approve_profile(latest["id"], approved)
        st.rerun()

    # ---------------- step 4: output
    st.markdown("**Step 4 · Output**")
    for k, png in enumerate(_preview_pngs(latest["pdf"]), 1):
        st.image(png, caption=f"Profile page {k}", width="stretch")
    c1, c2, c3 = st.columns(3)
    c1.download_button("Profile PDF", latest["pdf"], file_name=f"{sid} - profile.pdf", mime="application/pdf",
                       disabled=not latest["approved"], key=f"dlp_{sid}")
    c2.download_button("Profile JSON + generation log",
                       json.dumps({"profile": prof, "log": log}, ensure_ascii=False, indent=2),
                       file_name=f"{sid} - profile and log.json", mime="application/json", key=f"dlj_{sid}")
    cv = store.get_cv(sid)
    packet_ready = bool(latest["approved"] and cv and cv["checked"])
    if packet_ready:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(f"{sid} - profile.pdf", latest["pdf"])
            z.writestr(f"{sid} - CV.pdf", cv["pdf"])
        c3.download_button("Recruiter packet (profile + CV)", buf.getvalue(), file_name=f"{sid} - packet.zip",
                           mime="application/zip", key=f"dlz_{sid}")
    else:
        c3.caption("Packet: needs an approved profile and a CV marked as checked.")
