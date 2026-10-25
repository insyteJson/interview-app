"""Automatic ★ for the recruiter pool.

Rule: the interview is complete, the CV is uploaded, and there are at least MIN_SITUATIONS accepted situations from
at least MIN_ROLES different roles (roles in which at least one situation was accepted).

A star is only a pre-selection: recruiters still see a session only once its profile is approved and its CV is
marked as checked. Every session is judged once; a star removed by hand is never added back. Sessions that were
already finished when the rule was switched on are left alone until the researcher applies the rule to them.
"""

from __future__ import annotations

MIN_SITUATIONS = 4
MIN_ROLES = 2
BASELINE = "__baseline__"


def counts(state: dict) -> tuple[int, int]:
    incs = state.get("incidents") or []
    return len(incs), len({i.get("position_label") for i in incs})


def qualifies(sess: dict, has_cv: bool) -> tuple[bool, str]:
    n, roles = counts(sess["state"])
    ok = sess["status"] == "complete" and has_cv and n >= MIN_SITUATIONS and roles >= MIN_ROLES
    return ok, f"{n} situations from {roles} roles" + ("" if has_cv else ", no CV")


def _eligible(sess: dict, has_cv: bool) -> bool:
    return sess["status"] == "complete" and has_cv


def ensure_baseline(store, exclude: str | None = None) -> None:
    """On first use: mark every session that is already complete with a CV as 'existing' (left alone)."""
    decided = store.list_auto_decisions()
    if BASELINE in decided:
        return
    cvs = store.cvs_meta()
    for s in store.list_sessions():
        if s["id"] != exclude and s["id"] not in decided and _eligible(s, s["id"] in cvs):
            store.record_auto_decision(s["id"], -1, "finished before the rule was switched on")
    store.record_auto_decision(BASELINE, 0, "rule switched on")


def evaluate(store, sid: str) -> bool | None:
    """Judges one session once. Returns True if it was starred now, False if it did not qualify, None if it is
    not ready to be judged yet or was judged before."""
    ensure_baseline(store, exclude=sid)
    if sid in store.list_auto_decisions():
        return None
    sess = store.get_session(sid)
    has_cv = store.get_cv(sid) is not None
    if not sess or not _eligible(sess, has_cv):
        return None
    ok, reason = qualifies(sess, has_cv)
    if ok:
        store.set_favorite(sid, True)
    store.record_auto_decision(sid, 1 if ok else 0, reason)
    return ok


def evaluate_all(store) -> int:
    """Judges every session that is complete with a CV and not judged yet. Returns how many were starred."""
    ensure_baseline(store)
    decided = store.list_auto_decisions()
    cvs = store.cvs_meta()
    starred = 0
    for s in store.list_sessions():
        if s["id"] in decided or not _eligible(s, s["id"] in cvs):
            continue
        starred += bool(evaluate(store, s["id"]))
    return starred


def apply_to_existing(store) -> tuple[int, int]:
    """Researcher's choice: judge the sessions marked 'existing'. Sessions already starred stay starred."""
    decided = store.list_auto_decisions()
    cvs = store.cvs_meta()
    favs = store.list_favorites()
    starred = checked = 0
    for s in store.list_sessions():
        d = decided.get(s["id"])
        if not d or d["starred"] != -1:
            continue
        checked += 1
        ok, reason = qualifies(s, s["id"] in cvs)
        if ok and s["id"] not in favs:
            store.set_favorite(s["id"], True)
            starred += 1
        store.record_auto_decision(s["id"], 1 if ok else 0, reason + " (applied to an existing session)")
    return starred, checked
