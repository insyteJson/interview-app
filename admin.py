"""Researcher view: sessions, incidents, turn log with audit marks, exports."""

from __future__ import annotations

import hmac
import io

import pandas as pd
import streamlit as st

AUDIT_OPTIONS = ["", "ok", "deviation"]


def _check_password(expected: str | None) -> bool:
    if not expected:
        st.error("ADMIN_PASSWORD is not configured.")
        return False
    if st.session_state.get("admin_ok"):
        return True
    pw = st.text_input("Researcher password", type="password", key="admin_pw")
    if pw and hmac.compare_digest(pw, str(expected)):
        st.session_state["admin_ok"] = True
        st.rerun()
    elif pw:
        st.error("Wrong password.")
    return False


def _readiness(store, sid: str, sess: dict, favs: set) -> dict:
    """What a session still needs before recruiters can see it."""
    prof = store.get_approved_profile(sid)
    profs = store.list_profiles(sid)
    cv = store.get_cv(sid)
    rating = store.get_rating(sid)
    r = {"complete": sess["status"] == "complete", "approved": bool(prof), "cv": bool(cv),
         "cv_checked": bool(cv and cv["checked"]), "fav": sid in favs, "profile": prof, "profiles": profs,
         "rating": rating}
    r["ready"] = r["complete"] and r["approved"] and r["cv_checked"]
    return r


def _overview_row(s: dict, rd: dict) -> dict:
    state = s["state"]
    profs = rd["profiles"]
    prof = ("✓ approved" if rd["approved"] else "generated, not approved" if profs else "–")
    cv = "✓ checked" if rd["cv_checked"] else ("uploaded, not checked" if rd["cv"] else "–")
    q = rd["rating"]["status"].replace("_", " ") if rd["rating"] else "–"
    return {"★": "★" if rd["fav"] else "", "code": s["id"], "interview": s["status"],
            "roles": len(state.get("positions") or []), "situations": len(state.get("incidents") or []),
            "profile": prof, "CV": cv, "questionnaire": q,
            "ready for recruiters": "✓" if rd["fav"] and rd["ready"] else ("★ not ready" if rd["fav"] else ""),
            "last activity": s["updated_at"]}


def render_admin(store, admin_password: str | None, make_generator=lambda: None, base_url: str | None = None) -> None:
    st.markdown("<style>.block-container, [data-testid='stMainBlockContainer'] {max-width: 1400px;}</style>",
                unsafe_allow_html=True)
    st.title("Researcher view")
    if not _check_password(admin_password):
        return

    all_sessions = store.list_sessions()
    if not all_sessions:
        st.info("No sessions yet. A session appears once a participant has opened their link and agreed to take part.")
        return
    favs = store.list_favorites()
    ready = {s["id"]: _readiness(store, s["id"], s, favs) for s in all_sessions}
    reviews = store.list_reviews()

    # ---- study at a glance
    m = st.columns(5)
    m[0].metric("Interviews complete", f"{sum(s['status'] == 'complete' for s in all_sessions)} / {len(all_sessions)}")
    m[1].metric("★ Ready for recruiters", f"{sum(r['fav'] and r['ready'] for r in ready.values())} / {len(favs)}",
                help="Starred sessions with a complete interview, an approved profile and a checked CV.")
    m[2].metric("Candidate questionnaires", sum(bool(r["rating"]) and r["rating"]["status"] == "complete"
                                                for r in ready.values()))
    m[3].metric("Recruiters", len(store.list_recruiters()))
    m[4].metric("Recruiter ratings", sum(x["status"] == "complete" for x in reviews))

    t_sessions, t_recruiters, t_exports, t_emails = st.tabs(
        [":material/list_alt: Sessions", ":material/groups: Recruiter study", ":material/download: Exports",
         ":material/mail: Emails"])
    with t_sessions:
        _sessions_tab(store, all_sessions, favs, ready, make_generator)
    with t_recruiters:
        render_recruiter_section(store, base_url)
    with t_exports:
        _exports_tab(store)
    with t_emails:
        _emails_tab(store)


def _sessions_tab(store, all_sessions, favs, ready, make_generator) -> None:
    show = st.segmented_control("Show", ["All", "★ Favourites", "Ready for recruiters"], default="All",
                                key="admin_filter", label_visibility="collapsed")
    rows = sorted(all_sessions, key=lambda s: s["id"] not in favs)       # favourites first, stable otherwise
    if show == "★ Favourites":
        rows = [s for s in rows if s["id"] in favs]
    elif show == "Ready for recruiters":
        rows = [s for s in rows if ready[s["id"]]["fav"] and ready[s["id"]]["ready"]]
    if rows:
        st.dataframe(pd.DataFrame([_overview_row(s, ready[s["id"]]) for s in rows]), hide_index=True, width="stretch")
    else:
        st.info("Nothing matches this filter. Open a session below and switch on ★ Favourite.")
        rows = all_sessions

    st.divider()
    sid = st.selectbox("Open session", [s["id"] for s in rows], key="admin_sid")
    sess = store.get_session(sid)
    state = sess["state"]
    turns = store.get_turns(sid)
    rd = _readiness(store, sid, sess, favs)

    head, star = st.columns([3, 2], vertical_alignment="center")
    head.subheader(f"Session {sid}")
    fav = star.toggle("★ Favourite (use for recruiters)", value=sid in favs, key=f"fav_{sid}")
    if fav != (sid in favs):
        store.set_favorite(sid, fav)
        st.rerun()
    checks = [("Interview complete", rd["complete"]), ("Profile approved (Profile tab, step 4)", rd["approved"]),
              ("CV checked by hand (CV tab)", rd["cv_checked"])]
    st.markdown("  ".join(f"{'✅' if ok else '⬜'} {label}" for label, ok in checks))
    if fav and not rd["ready"]:
        st.warning("★ but not ready, so recruiters do not see it yet: it needs a complete interview, an approved "
                   "profile and a CV marked as checked.")
    elif fav:
        st.success("In the recruiter pool.")

    t_int, t_cv, t_prof, t_q = st.tabs(["Interview", "CV", "Profile", "Questionnaire"])
    with t_int:
        _interview_tab(store, sid, state, turns)
    with t_cv:
        _cv_tab(store, sid)
    with t_prof:
        from profile_ui import render_profile_section
        render_profile_section(store, sid, state, turns, make_generator)
    with t_q:
        render_rating_section(store, sid)


def _interview_tab(store, sid: str, state: dict, rows: list[dict]) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Status", state["status"])
    c2.metric("Turn", state["turn"])
    pos = state["positions"][state["pos_idx"]] if state["positions"] and state["pos_idx"] < len(state["positions"]) else None
    c3.metric("Position", f"{state['pos_idx'] + 1}/{len(state['positions'])}" if pos else "-")
    c4.metric("Phase", state["phase"])
    tokens_in = sum((r["meta"] or {}).get("input_tokens", 0) or 0 for r in rows)
    tokens_out = sum((r["meta"] or {}).get("output_tokens", 0) or 0 for r in rows)
    st.caption(f"Model: {state.get('model')} · prompt version: {state.get('prompt_version')} · "
               f"dimension: {state.get('dimension')} · level: {state.get('level')} · "
               f"tokens: {tokens_in:,} in / {tokens_out:,} out")

    with st.expander(f"Positions ({len(state['positions'] or [])})", expanded=False):
        st.dataframe(pd.DataFrame(state["positions"]) if state["positions"] else pd.DataFrame(),
                     hide_index=True, width="stretch")
    with st.expander(f"Accepted incidents ({len(state['incidents'] or [])})", expanded=False):
        if state["incidents"]:
            st.dataframe(pd.DataFrame(state["incidents"]), hide_index=True, width="stretch")
        else:
            st.caption("None yet.")

    st.subheader("Turn log and audit")
    df = pd.DataFrame([{
        "id": r["id"], "turn": r["turn"], "phase": r["phase_before"],
        "answer": r["answer_raw"], "label": (r["classification"] or {}).get("label"),
        "rationale": (r["classification"] or {}).get("rationale"),
        "rule": r["rule"], "question shown": r["question_shown"],
        "audit": r["audit"] or "", "note": r["audit_note"] or "",
    } for r in rows])
    edited = st.data_editor(
        df, hide_index=True, width="stretch", key=f"editor_{sid}",
        disabled=[c for c in df.columns if c not in ("audit", "note")],
        column_config={
            "id": None,
            "audit": st.column_config.SelectboxColumn("audit", options=AUDIT_OPTIONS),
            "answer": st.column_config.TextColumn(width="medium"),
            "question shown": st.column_config.TextColumn(width="medium"),
        },
    )
    a, b = st.columns([1, 2], vertical_alignment="center")
    if a.button("Save audit marks", type="primary"):
        changed = 0
        for (_, old), (_, new) in zip(df.iterrows(), edited.iterrows()):
            if old["audit"] != new["audit"] or old["note"] != new["note"]:
                store.set_audit(int(new["id"]), new["audit"] or "", new["note"] or "")
                changed += 1
        st.success(f"Saved {changed} change(s).")
    audited = edited[edited["audit"].isin(["ok", "deviation"])]
    if len(audited):
        dev = int((audited["audit"] == "deviation").sum())
        b.metric("Protocol adherence (audited turns)", f"{100 * (len(audited) - dev) / len(audited):.0f}%",
                 help=f"{len(audited)} audited, {dev} deviation(s)")

    st.markdown("**Export this session**")
    c = st.columns(3)
    c[0].download_button("Session as JSON", store.export_session(sid), file_name=f"session-{sid}.json",
                         mime="application/json", width="stretch")
    buf = io.StringIO()
    df.drop(columns=["id"]).to_csv(buf, index=False)
    c[1].download_button("Turn log as CSV", buf.getvalue(), file_name=f"turns-{sid}.csv", mime="text/csv",
                         width="stretch")
    if state["incidents"]:
        buf2 = io.StringIO()
        pd.DataFrame(state["incidents"]).to_csv(buf2, index=False)
        c[2].download_button("Incidents as CSV", buf2.getvalue(), file_name=f"incidents-{sid}.csv", mime="text/csv",
                             width="stretch")


def _cv_tab(store, sid: str) -> None:
    cvrow = store.get_cv(sid)
    if not cvrow:
        st.caption("No CV submitted yet.")
        return
    with st.expander("Blackout report", expanded=False):
        st.write(cvrow["report"])
    a, b = st.columns([1, 2], vertical_alignment="center")
    a.download_button("Download anonymised CV (PDF)", cvrow["pdf"], file_name=f"cv-{sid}.pdf", mime="application/pdf",
                      width="stretch")
    checked = b.checkbox("I checked this CV by hand (nothing identifying left)", value=bool(cvrow["checked"]),
                         key=f"cv_checked_{sid}")
    if checked != bool(cvrow["checked"]):
        store.mark_cv_checked(sid, checked)
        st.rerun()


def _exports_tab(store) -> None:
    st.markdown("Everything for the analysis. Per-session exports (JSON, turn log, incidents) are in "
                "**Sessions → Interview**.")
    c = st.columns(2)
    with c[0], st.container(border=True):
        st.markdown("**Recruiter ratings (DO1)**  \nOne row per recruiter × candidate, with seconds per screen and a "
                    "flag for identical answers on both documents.")
        table = reviews_table(store)
        if len(table):
            buf = io.StringIO()
            table.to_csv(buf, index=False)
            st.download_button("Download recruiter_ratings.csv", buf.getvalue(), file_name="recruiter_ratings.csv",
                               mime="text/csv", key="rv_csv", type="primary", width="stretch")
        else:
            st.caption("No recruiter ratings yet.")
    with c[1], st.container(border=True):
        st.markdown("**Candidate questionnaires (DO2)**  \nOne row per candidate, with document order and seconds "
                    "per step.")
        table = ratings_table(store)
        if len(table):
            buf = io.StringIO()
            table.to_csv(buf, index=False)
            st.download_button("Download questionnaires.csv", buf.getvalue(), file_name="questionnaires.csv",
                               mime="text/csv", key="rcsv", type="primary", width="stretch")
        else:
            st.caption("No questionnaires yet.")


def _emails_tab(store) -> None:
    st.caption("Stored apart from all study data, without recruiter codes; not part of the thesis data.")
    sign = store.list_signups()
    if not sign:
        st.info("No signups yet.")
        return
    st.dataframe(pd.DataFrame(sign), hide_index=True, width="stretch")
    buf = io.StringIO()
    pd.DataFrame(sign).to_csv(buf, index=False)
    c1, c2, c3 = st.columns([2, 2, 1], vertical_alignment="bottom")
    c1.download_button("Signups as CSV", buf.getvalue(), file_name="signups.csv", mime="text/csv", key="su_csv",
                       width="stretch")
    sel = c2.selectbox("Remove (unsubscribe)", [f"{x['id']} · {x['email']}" for x in sign], key="su_sel")
    if c3.button("Remove", key="su_del", width="stretch"):
        store.delete_signup(int(sel.split(" · ")[0]))
        st.rerun()


def ratings_table(store) -> pd.DataFrame:
    """One row per participant, one column per answer: the input for the DO2 analysis."""
    import rating as rt
    out = []
    for r in store.list_ratings():
        row = {"code": r["session_id"], "instrument": r["instrument"], "order": r["doc_order"],
               "profile_id": r["profile_id"], "status": r["status"], "started": r["started_at"],
               "completed": r["completed_at"]}
        for s in rt.screens(r["doc_order"]):
            for it in rt.items(s):
                k = rt.answer_key(s, it[0])
                row[k] = (r["answers"] or {}).get(k)
            row[f"sec_{rt.screen_id(s)}"] = ((r["screens"] or {}).get(rt.screen_id(s)) or {}).get("seconds")
        out.append(row)
    return pd.DataFrame(out)


def render_rating_section(store, sid: str) -> None:
    import rating as rt
    ready = bool(store.get_approved_profile(sid) and store.get_cv(sid))
    st.markdown(f"**Link to send:** `?rate={sid}` · order for this code: **{rt.doc_order(sid)}**")
    if not ready:
        st.caption("The link opens the questionnaire once this session has an approved profile and a CV. "
                   "Until then the participant sees 'not ready yet'.")
    shown = store.get_approved_profile(sid)
    profs = store.list_profiles(sid)
    if shown:
        st.markdown(f"The link shows profile **#{shown['id']} ({shown['version']})**, the newest approved one.")
    if profs and (not shown or profs[0]["id"] != shown["id"]):
        st.warning(f"The newest generation #{profs[0]['id']} ({profs[0]['version']}) is not approved, so the link does "
                   "not show it. Approve it in Step 4 above.")
    r = store.get_rating(sid)
    if r and shown and r["profile_id"] != shown["id"] and r["answers"]:
        st.error(f"This participant already answered on profile #{r['profile_id']}. Their answers stay on that "
                 "version. To re-rate on the newest one, delete their answers below and send the link again.")
    if not r:
        st.caption("Not started.")
    else:
        sc = rt.screens(r["doc_order"])
        st.caption(f"Status: {r['status']} · {len(r['screens'] or {})} of {len(sc)} steps saved · "
                   f"rated profile #{r['profile_id']} · started {r['started_at']:%d.%m.%Y %H:%M} UTC")
        st.dataframe(pd.DataFrame([{"answer": k, "value": str(v)} for k, v in (r["answers"] or {}).items()]),
                     hide_index=True, width="stretch")
        if st.checkbox("Delete this participant's questionnaire answers (withdrawal or test run)", key=f"rdel_{sid}"):
            if st.button("Delete answers", key=f"rdelb_{sid}"):
                store.delete_rating(sid)
                st.rerun()


INVITE_STATUS = {None: "🟢 open", "used": "✓ used", "expired": "⏱ expired", "revoked": "✕ revoked",
                 "unknown": "?"}


def render_recruiter_section(store, base_url: str | None = None) -> None:
    import review as rv
    invs = store.list_invites()
    recs = store.list_recruiters()
    allr = store.list_reviews()

    left, right = st.columns([2, 3], gap="large")
    with left, st.container(border=True):
        st.markdown("**New invite link**")
        st.caption(f"Single use, valid for 14 days. Recruiters can also create up to {rv.MAX_SHARES} links each for "
                   "colleagues.")
        if st.button("Create invite link", key="inv_new", type="primary", icon=":material/add_link:",
                     width="stretch"):
            st.session_state["inv_last"] = store.create_invite("researcher")
        if st.session_state.get("inv_last"):
            st.code(rv._invite_link(st.session_state["inv_last"], base_url), language=None, wrap_lines=True)
            st.caption("Copy with the icon at the right of the box.")
    with right:
        st.markdown("**Invites**")
        if invs:
            st.dataframe(pd.DataFrame([{
                "status": INVITE_STATUS.get(store.invite_problem(i), "?"), "token": i["token"],
                "created by": i["created_by"], "created": i["created_at"], "used by": i["used_by"] or ""}
                for i in invs]), hide_index=True, width="stretch", height=min(38 + 35 * len(invs), 260))
            open_tokens = [i["token"] for i in invs if not store.invite_problem(i)]
            if open_tokens:
                with st.expander("Revoke an open invite"):
                    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
                    tok = c1.selectbox("Open invite", open_tokens, key="inv_revoke_sel")
                    if c2.button("Revoke", key="inv_revoke", width="stretch"):
                        store.revoke_invite(tok)
                        st.rerun()
        else:
            st.caption("No invites yet.")

    st.divider()
    st.markdown("**Recruiters**")
    if recs:
        st.dataframe(pd.DataFrame([{
            "code": r["code"], "invited by": r["invited_by"], "joined": r["created_at"],
            "completed": sum(x["recruiter"] == r["code"] and x["status"] == "complete" for x in allr),
            "skipped (recognised)": sum(x["recruiter"] == r["code"] and x["status"] == "skipped" for x in allr),
            "in progress": sum(x["recruiter"] == r["code"] and x["status"] == "in_progress" for x in allr),
            "experience": r["experience"] or "", "domain": r["domain"] or ""} for r in recs]),
            hide_index=True, width="stretch")
        with st.expander("Withdrawal or test run: delete a recruiter's ratings"):
            c1, c2, c3 = st.columns([3, 1, 1], vertical_alignment="bottom")
            who = c1.selectbox("Recruiter", [r["code"] for r in recs], key="rv_del_sel")
            ok = c2.checkbox("Confirm", key="rv_del_ok")
            if c3.button("Delete", key="rv_del", disabled=not ok, width="stretch"):
                store.delete_reviews(who)
                st.rerun()
    else:
        st.caption("No recruiters yet.")

    st.divider()
    st.markdown(f"**Balance per candidate** · pool = ★ sessions that are ready; at most {rv.MAX_PER_CANDIDATE} "
                "recruiters each")
    pool = {p["session_id"] for p in rv.ready_pool(store)}
    sids = sorted(pool | {x["session_id"] for x in allr})
    if sids:
        st.dataframe(pd.DataFrame([{
            "candidate": s, "in pool": "✓" if s in pool else "–",
            "shown to": f"{sum(x['session_id'] == s for x in allr)} / {rv.MAX_PER_CANDIDATE}",
            "completed": sum(x["session_id"] == s and x["status"] == "complete" for x in allr),
            "profile first": sum(x["session_id"] == s and x["status"] == "complete" and
                                 x["doc_order"] == "profile-first" for x in allr),
            "CV first": sum(x["session_id"] == s and x["status"] == "complete" and
                            x["doc_order"] == "cv-first" for x in allr)} for s in sids]),
            hide_index=True, width="stretch")
    else:
        st.caption("The pool is empty. Star sessions in the Sessions tab and make them ready.")


def reviews_table(store) -> pd.DataFrame:
    """Long format for the DO1 analysis: one row per recruiter and candidate."""
    import review as rv
    recs = {r["code"]: r for r in store.list_recruiters()}
    out = []
    for x in store.list_reviews():
        a = x["answers"] or {}
        sc = x["screens"] or {}
        row = {"recruiter": x["recruiter"], "candidate": x["session_id"], "profile_id": x["profile_id"],
               "order": x["doc_order"], "status": x["status"], "started": x["started_at"],
               "completed": x["completed_at"]}
        for doc in ("profile", "cv"):
            for k in rv.doc_keys(doc):
                row[k] = a.get(k)
        row.update(pref=a.get("pref"), pref_doc=a.get("pref_doc"), missing=a.get("missing"))
        for s in ("A", "B", "compare"):
            row[f"sec_{s}"] = (sc.get(s) or {}).get("seconds")
        p = [a.get(k) for k in rv.doc_keys("profile")]
        c = [a.get(k) for k in rv.doc_keys("cv")]
        row["flag_identical"] = None not in p + c and len(set(p + c)) == 1
        r = recs.get(x["recruiter"]) or {}
        row.update(experience=r.get("experience"), domain=r.get("domain"))
        out.append(row)
    return pd.DataFrame(out)
