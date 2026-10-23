"""Researcher view, part 2: redaction pass, same-event review, profile generation, operator check, PDF and packet."""

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


def _pairs(pairs) -> set[tuple[str, str]]:
    return {tuple(sorted(p)) for p in pairs or []}


def _merge_step(store, sid: str, state: dict, rules) -> tuple[bool, list]:
    """Step 2: the researcher decides which flagged pairs are the same event. Returns (ready, saved pairs)."""
    st.markdown("**Step 2 · Same event told twice.** A candidate may tell one event in answer to two questions. "
                "Pairs from the same role are listed, most similar first; tick only pairs that describe the same "
                "event. A ticked pair becomes one entry, shown under both themes and counted once.")
    cands = pg.duplicate_candidates(state, rules)
    saved = store.get_merges(sid)
    saved_pairs = _pairs(saved["pairs"]) if saved else set()
    if not cands:
        st.caption("No two moments share a role, so there is nothing to compare.")
        return True, []
    by_id = {i["id"]: i for i in state.get("incidents") or []}
    chosen = []
    for c in cands:
        a, b = by_id[c["a"]], by_id[c["b"]]
        flag = " · likely the same" if c["likely"] else ""
        title = (f"{c['a']} ({pg.HEADINGS[a['dimension']]}) and {c['b']} ({pg.HEADINGS[b['dimension']]}) · "
                 f"{c['role']} · overlap {c['score']:.0%}{flag}")
        with st.expander(title, expanded=c["likely"]):
            col1, col2 = st.columns(2)
            for col, inc in ((col1, a), (col2, b)):
                col.markdown(f"**{inc['id']}**")
                for name, key in (("Situation", "situation"), ("Action", "action"), ("Outcome", "outcome")):
                    col.markdown(f"*{name}:* {pg.apply_rules(inc.get(key), rules) or '—'}")
            if st.checkbox("These describe the same event", value=(c["a"], c["b"]) in saved_pairs,
                           key=f"dup_{sid}_{c['a']}_{c['b']}"):
                chosen.append([c["a"], c["b"]])
    if st.button("Save merge decisions", key=f"mergesave_{sid}"):
        store.save_merges(sid, chosen)
        st.rerun()
    current = saved is not None and _pairs(chosen) == saved_pairs
    if current:
        st.caption(f"Merge decisions saved {saved['confirmed_at']:%d.%m.%Y %H:%M} UTC: "
                   f"{len(saved_pairs)} pair(s) merged.")
    elif saved:
        st.warning("The ticks differ from the saved decisions. Save again before generating.")
    else:
        st.warning("Save the merge decisions (also when nothing is ticked) before generating.")
    return current, [list(p) for p in saved_pairs]


def _check_v2(prof: dict, log: dict) -> None:
    st.markdown("*Role purpose lines* (restated from the aim answer; a role without one shows none)")
    for r, plog in zip(prof["roles"], log.get("purposes", [])):
        c1, c2 = st.columns(2)
        c1.markdown(f"**{r['display']}**  \n> {plog.get('aim') or '—'}")
        if r.get("purpose"):
            c2.markdown(f"Purpose: {r['purpose']}")
            for w in plog.get("warnings", []):
                c2.warning(w)
        else:
            c2.caption(f"No purpose line: {plog.get('skipped', 'not generated')}")
    if "categories" in prof:
        st.markdown("*Recurring behaviour* (grouped by the model; check that the statement is true of every cited "
                    "situation and that the summary adds nothing)")
        by_num = {e["num"]: e for e in prof["entries"]}
        if not prof["categories"]:
            st.caption("No group: " + (log.get("categories", {}).get("skipped")
                                       or "the model found no behaviour recurring in two different roles."))
        for c in prof["categories"]:
            with st.expander(f"{c['statement']} · {c['situations']} situations · {c['roles']} roles", expanded=True):
                st.markdown(c["summary"])
                for m in c["members"]:
                    e = by_num[m]
                    st.caption(f"[{m}] {e['headline']} — {e['role']}")
        for w in log.get("categories", {}).get("warnings", []):
            st.warning(w)
    st.markdown("*Situations*")
    for x in prof["entries"]:
        themes = " + ".join(pg.HEADINGS[d] for d in x["dimensions"])
        ids = " + ".join(x["members"])
        num = f"[{x['num']}] " if "num" in x else ""
        with st.expander(f"{num}{ids} · {themes} · {x['headline']}"):
            c1, c2 = st.columns(2)
            c1.markdown("**Answers (after redaction)**" + (" — merged accounts" if len(x["members"]) > 1 else ""))
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


def _check_v1(prof: dict) -> None:
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
                for w in x.get("warnings", []):
                    c2.warning(w)


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
    texts = [", ".join(p.get("tools") or []) for p in positions] + [p.get("aim") or "" for p in positions]
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
            st.markdown(f"> Purpose answer: {pg.apply_rules(p.get('aim') or '—', rules)}")
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

    # ---------------- step 2: same event told twice
    merges_ready, merge_pairs = _merge_step(store, sid, state, rules)

    # ---------------- step 3: generation
    st.markdown(f"**Step 3 · Generate** ({pg.PROFILE_VERSION})")
    profs = store.list_profiles(sid)
    reason = st.text_input("Reason for this generation (logged)", key=f"reason_{sid}",
                           value="" if profs else "first generation")
    if st.button("Generate profile", type="primary", key=f"gen_{sid}",
                 disabled=not (saved_same and merges_ready and reason.strip())):
        gen = make_generator()
        if gen is None:
            st.error("ANTHROPIC_API_KEY is not configured.")
        else:
            bar = st.progress(0.0, text="Generating…")
            res = pg.generate_profile(state, rows, pg.parse_rules(red["rules"]), gen, merges=merge_pairs,
                                      progress=lambda k, n: bar.progress(k / n, text=f"Step {k + 1} of {n}…"))
            pdf = pg.render_pdf(res["profile"]) if res["ok"] else None
            store.add_profile(sid, pg.PROFILE_VERSION, res["ok"], reason.strip(), res["profile"], res["log"], pdf)
            st.rerun()
    if not profs:
        return

    # ---------------- step 4: operator check
    latest = profs[0]
    log = latest["log"]
    calls = ([a for i in log["incidents"] for a in i["attempts"]] + [a for p in log.get("purposes", []) for a in p["attempts"]]
             + list((log.get("categories") or {}).get("attempts", [])))
    attempts = len(calls)
    tok_in = sum(a.get("meta", {}).get("input_tokens", 0) for a in calls)
    tok_out = sum(a.get("meta", {}).get("output_tokens", 0) for a in calls)
    st.caption(f"{len(profs)} generation(s) logged · latest #{latest['id']} ({latest['version']}, {latest['reason']}) · "
               f"{attempts} model call(s) for {len(log['incidents'])} entries · tokens {tok_in:,} in / {tok_out:,} out")
    if latest["version"] != pg.PROFILE_VERSION:
        st.info(f"The latest profile was generated with {latest['version']}. Generate again to get {pg.PROFILE_VERSION}; "
                "the older profile and its PDF stay stored.")
    if not latest["ok"]:
        st.error("The latest generation failed the checks for at least one incident after 3 attempts, so no PDF "
                 "was produced. Fix the cause (usually a missing redaction rule) and generate again.")
        for i in log["incidents"]:
            if not i["passed"]:
                st.markdown(f"**{' + '.join(i.get('members') or [i['id']])}**")
                for a in i["attempts"]:
                    st.write(a.get("error") or a.get("fails"))
        cl = log.get("categories") or {}
        if cl.get("skipped") == "failed the checks after 3 attempts":
            st.markdown("**Recurring behaviour** (grouping failed its checks; generate again)")
            for a in cl.get("attempts", []):
                st.write(a.get("error") or a.get("fails"))
        return

    st.markdown("**Step 4 · Check every entry against the answers** (never edit by hand; regenerate instead)")
    prof = latest["profile"]
    if "entries" in prof:
        _check_v2(prof, log)
    else:
        _check_v1(prof)
    approved = st.checkbox("I checked every situation, purpose line and behaviour group against the answers. Nothing is "
                           "added, every label and tag meets its coding rule, every group statement is true of each "
                           "situation it cites, no personal name appears.",
                           value=bool(latest["approved"]), key=f"appr_{sid}_{latest['id']}")
    if approved != bool(latest["approved"]):
        store.approve_profile(latest["id"], approved)
        st.rerun()

    # ---------------- step 5: output
    st.markdown("**Step 5 · Output**")
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
