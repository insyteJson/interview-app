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


def render_admin(store, admin_password: str | None, make_generator=lambda: None) -> None:
    st.title("Interview sessions")
    if not _check_password(admin_password):
        return

    all_sessions = store.list_sessions()
    if not all_sessions:
        st.info("No sessions yet. A session appears once a participant has opened their link and agreed to take part.")
        return

    overview = pd.DataFrame([{
        "code": s["id"], "status": s["status"], "turns": s["state"].get("turn"),
        "positions": len(s["state"].get("positions") or []), "incidents": len(s["state"].get("incidents") or []),
        "started": s["created_at"], "last activity": s["updated_at"],
    } for s in all_sessions])
    st.dataframe(overview, hide_index=True, width="stretch")

    sid = st.selectbox("Session", [s["id"] for s in all_sessions], key="admin_sid")
    sess = store.get_session(sid)
    state = sess["state"]
    rows = store.get_turns(sid)

    # ---- state summary
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Status", state["status"])
    c2.metric("Turn", state["turn"])
    pos = state["positions"][state["pos_idx"]] if state["positions"] and state["pos_idx"] < len(state["positions"]) else None
    c3.metric("Position", f"{state['pos_idx'] + 1}/{len(state['positions'])}" if pos else "-")
    c4.metric("Phase", state["phase"])
    st.caption(f"Model: {state.get('model')} · prompt version: {state.get('prompt_version')} · "
               f"dimension: {state.get('dimension')} · level: {state.get('level')}")

    tokens_in = sum((r["meta"] or {}).get("input_tokens", 0) or 0 for r in rows)
    tokens_out = sum((r["meta"] or {}).get("output_tokens", 0) or 0 for r in rows)
    st.caption(f"Tokens so far: {tokens_in:,} in / {tokens_out:,} out")

    # ---- positions and incidents
    st.subheader("Positions")
    st.dataframe(pd.DataFrame(state["positions"]) if state["positions"] else pd.DataFrame(),
                 hide_index=True, width="stretch")

    st.subheader("Accepted incidents")
    if state["incidents"]:
        st.dataframe(pd.DataFrame(state["incidents"]), hide_index=True, width="stretch")
    else:
        st.caption("None yet.")

    # ---- turn log with audit
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
    if st.button("Save audit marks"):
        changed = 0
        for (_, old), (_, new) in zip(df.iterrows(), edited.iterrows()):
            if old["audit"] != new["audit"] or old["note"] != new["note"]:
                store.set_audit(int(new["id"]), new["audit"] or "", new["note"] or "")
                changed += 1
        st.success(f"Saved {changed} change(s).")

    audited = edited[edited["audit"].isin(["ok", "deviation"])]
    if len(audited):
        dev = int((audited["audit"] == "deviation").sum())
        st.metric("Protocol adherence (audited turns)", f"{100 * (len(audited) - dev) / len(audited):.0f}%",
                  help=f"{len(audited)} audited, {dev} deviation(s)")

    # ---- exports
    st.subheader("Export")
    st.download_button("Session as JSON", store.export_session(sid), file_name=f"session-{sid}.json",
                       mime="application/json")
    buf = io.StringIO()
    df.drop(columns=["id"]).to_csv(buf, index=False)
    st.download_button("Turn log as CSV", buf.getvalue(), file_name=f"turns-{sid}.csv", mime="text/csv")
    if state["incidents"]:
        buf2 = io.StringIO()
        pd.DataFrame(state["incidents"]).to_csv(buf2, index=False)
        st.download_button("Incidents as CSV", buf2.getvalue(), file_name=f"incidents-{sid}.csv", mime="text/csv")

    # ---- anonymised CV
    st.subheader("Anonymised CV")
    cvrow = store.get_cv(sid)
    if not cvrow:
        st.caption("No CV submitted yet.")
    else:
        st.write(cvrow["report"])
        st.download_button("Download anonymised CV (PDF)", cvrow["pdf"], file_name=f"cv-{sid}.pdf", mime="application/pdf")
        checked = st.checkbox("I checked this CV by hand (nothing identifying left)", value=bool(cvrow["checked"]),
                              key=f"cv_checked_{sid}")
        if checked != bool(cvrow["checked"]):
            store.mark_cv_checked(sid, checked)
            st.rerun()

    # ---- profile for recruiters
    st.divider()
    from profile_ui import render_profile_section
    render_profile_section(store, sid, state, rows, make_generator)

    # ---- candidate questionnaire
    st.divider()
    render_rating_section(store, sid)


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
    st.subheader("Candidate questionnaire")
    ready = bool(store.get_approved_profile(sid) and store.get_cv(sid))
    st.markdown(f"Link to send: `?rate={sid}` · order for this code: **{rt.doc_order(sid)}**")
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
    table = ratings_table(store)
    if len(table):
        buf = io.StringIO()
        table.to_csv(buf, index=False)
        st.download_button("All questionnaires as CSV (one row per participant)", buf.getvalue(),
                           file_name="questionnaires.csv", mime="text/csv", key="rcsv")
