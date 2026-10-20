"""The role form as the participant sees it (Streamlit). Fields per category come from roles.CATEGORIES.

Kept deliberately small: one line per entry, labels hidden behind placeholders, one heading per category that is
in use, and three small "add" buttons. It starts with one empty job row.
"""

from __future__ import annotations

import streamlit as st

import roles

_CSS = """<style>
.rf-cat{font-weight:600;font-size:.92rem;margin:.55rem 0 .05rem 0}
.rf-cat span{font-weight:400;color:#6b7280;font-size:.82rem;margin-left:.35rem}
.rf-note{color:#6b7280;font-size:.82rem;margin:.1rem 0 .3rem 0}
[class*="st-key-rfrow"] [data-testid="InputInstructions"]{display:none}
[data-testid="stMainBlockContainer"]{max-width:60rem !important}
[data-stale="true"],[data-stale="true"] *{opacity:1 !important;transition:none !important;animation:none !important}
@media (max-width: 640px){
 [class*="st-key-rfrow"] [data-testid="stHorizontalBlock"]{flex-wrap:wrap !important;row-gap:.45rem !important}
 [class*="st-key-rfrow"] [data-testid="stColumn"]{min-width:0 !important;width:auto !important;flex:1 1 calc(50% - .5rem) !important}
 [class*="st-key-rfrow"] [data-testid="stColumn"]:first-child{flex:1 1 calc(100% - 3rem) !important;order:0}
 [class*="st-key-rfrow"] [data-testid="stColumn"]:nth-child(2){flex:1 1 100% !important;order:2}
 [class*="st-key-rfrow"] [data-testid="stColumn"]:nth-child(3){order:3}
 [class*="st-key-rfrow"] [data-testid="stColumn"]:nth-child(4){order:4}
 [class*="st-key-rfrow"] [data-testid="stColumn"]:last-child{flex:0 0 2rem !important;order:1}
 [class*="st-key-rfrow_job"] [data-testid="stColumn"]:nth-child(3){flex:1 1 100% !important;order:3}
 [class*="st-key-rfrow_job"] [data-testid="stColumn"]:nth-child(5){order:5}
 [class*="st-key-rfrow_job"] [data-testid="stColumn"]:last-child{flex:0 0 2rem !important;order:1}
}
</style>"""


def _rows() -> list[dict]:
    if "rf_rows" not in st.session_state:
        st.session_state["rf_rows"] = [{"id": 0, "cat": "job"}]
        st.session_state["rf_next"] = 1
    return st.session_state["rf_rows"]


def _add(cat: str) -> None:
    rows = _rows()
    rows.append({"id": st.session_state["rf_next"], "cat": cat})
    st.session_state["rf_next"] += 1


def _remove(rid: int) -> None:
    st.session_state["rf_rows"] = [r for r in _rows() if r["id"] != rid]


def collect() -> list[dict]:
    out = []
    for r in _rows():
        k = f"rf_{r['id']}_"
        out.append({"cat": r["cat"], "kind": st.session_state.get(k + "kind") or "",
                    "title": st.session_state.get(k + "title", ""),
                    "org": st.session_state.get(k + "org", ""), "start": st.session_state.get(k + "start", ""),
                    "end": st.session_state.get(k + "end", "")})
    return out


def clear() -> None:
    for key in [k for k in st.session_state if str(k).startswith("rf_")]:
        del st.session_state[key]


def _row(r: dict) -> None:
    spec = roles.CATEGORIES[r["cat"]]
    k = f"rf_{r['id']}_"
    box = st.container(key=f"rfrow_{r['cat']}_{r['id']}")
    if r["cat"] == "job":
        cols = box.columns([2.6, 2.4, 2.1, 1.2, 1.2, 0.45], gap="small", vertical_alignment="center")
        cols[0].selectbox("Type", roles.JOB_TYPES, index=None, key=k + "kind", placeholder="Type",
                          label_visibility="collapsed", on_change="ignore")
        cols = cols[1:]
    elif spec["period"] == "none":
        cols = box.columns([4, 3.6, 0.45], gap="small", vertical_alignment="center")
    else:
        cols = box.columns([3, 2.6, 1.45, 1.45, 0.45], gap="small", vertical_alignment="center")
    cols[0].text_input(spec["title"][0], key=k + "title", placeholder=spec["title"][1],
                       label_visibility="collapsed", on_change="ignore")
    cols[1].text_input(spec["org"][0], key=k + "org", placeholder=spec["org"][1], label_visibility="collapsed", on_change="ignore")
    if spec["period"] != "none":
        cols[2].text_input("From", key=k + "start", placeholder="from", label_visibility="collapsed", on_change="ignore")
        cols[3].text_input("To", key=k + "end", placeholder="to / now", label_visibility="collapsed", on_change="ignore")
    cols[-1].button("✕", key=k + "rm", type="tertiary", help="Remove", on_click=_remove, args=(r["id"],))


def render(max_roles: int) -> list[dict] | None:
    """Draw the form. Returns the rows when 'Continue' is clicked and they pass the checks, else None."""
    st.markdown(_CSS, unsafe_allow_html=True)
    rows = _rows()
    for cat in roles.ORDER:
        mine = [r for r in rows if r["cat"] == cat]
        if not mine:
            continue
        spec = roles.CATEGORIES[cat]
        st.markdown(f"<p class='rf-cat'>{spec['plural']}<span>{spec['hint']}</span></p>", unsafe_allow_html=True)
        for r in mine:
            _row(r)
    if any(roles.CATEGORIES[r["cat"]]["period"] != "none" for r in rows):
        st.markdown("<p class='rf-note'>Dates as 03/2021 or 2021; write “now” if it is ongoing.</p>",
                    unsafe_allow_html=True)
    full = len(rows) >= max_roles
    with st.container(horizontal=True, gap="small"):
        for cat in roles.ORDER:
            st.button(roles.CATEGORIES[cat]["name"], key=f"rf_add_{cat}", icon=":material/add:", type="tertiary",
                      on_click=_add, args=(cat,), disabled=full)
    if st.button("Continue", type="primary", key="rf_submit"):
        positions, errors = roles.normalise(collect())
        if len(positions) > max_roles:
            errors.append(f"Please keep it to {max_roles} entries at most.")
        if errors:
            st.error("\n".join(f"- {e}" for e in errors))
            return None
        return collect()
    return None
