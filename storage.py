"""Persistence. SQLite locally, any SQLAlchemy URL (e.g. Supabase Postgres) in production.

Every turn is written in one transaction together with the new session state, guarded by an
optimistic version check, so a double submit or a second browser tab cannot corrupt a session.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import (JSON, event, Column, DateTime, Integer, LargeBinary, MetaData, String, Table, Text, UniqueConstraint,
                        create_engine, insert, select, update)

# Bump whenever a table or a Store method is added: app.py keys its cached Store on this value, so a redeploy
# builds a fresh Store (and creates new tables) instead of reusing one created by the previous code.
SCHEMA_VERSION = "2026-10-25.autostar-v1"

metadata = MetaData()

sessions = Table(
    "sessions", metadata,
    Column("id", String(40), primary_key=True),
    Column("status", String(20), nullable=False),
    Column("version", Integer, nullable=False, default=1),
    Column("state", JSON, nullable=False),
    Column("consent_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

turns = Table(
    "turns", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", String(40), nullable=False, index=True),
    Column("turn", Integer, nullable=False),
    Column("answer_raw", Text),
    Column("question_shown", Text, nullable=False),
    Column("rule", String(40)),
    Column("qid", String(30)),
    Column("phase_before", String(20)),
    Column("classification", JSON),
    Column("meta", JSON),
    Column("accepted", Integer, default=0),
    Column("declined", Integer, default=0),
    Column("audit", String(20), default=""),
    Column("audit_note", Text, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("session_id", "turn", name="uq_session_turn"),
)


cvs = Table(
    "cvs", metadata,
    Column("session_id", String(40), primary_key=True),
    Column("pdf", LargeBinary, nullable=False),
    Column("report", JSON),
    Column("checked", Integer, default=0),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


redactions = Table(
    "redactions", metadata,
    Column("session_id", String(40), primary_key=True),
    Column("rules", Text, nullable=False),
    Column("confirmed_at", DateTime(timezone=True), nullable=False),
)

profiles = Table(
    "profiles", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", String(40), nullable=False, index=True),
    Column("version", String(40), nullable=False),
    Column("ok", Integer, nullable=False),
    Column("reason", Text),
    Column("profile", JSON, nullable=False),
    Column("log", JSON, nullable=False),
    Column("pdf", LargeBinary),
    Column("approved", Integer, default=0),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

# resume-v2: researcher decisions on which incidents describe the same event (pairs of incident ids).
# Created automatically on the next start (create_all adds missing tables, existing ones are untouched).
merges = Table(
    "merges", metadata,
    Column("session_id", String(40), primary_key=True),
    Column("pairs", JSON, nullable=False),
    Column("confirmed_at", DateTime(timezone=True), nullable=False),
)


# rating-v1: the candidate questionnaire (DO2), opened through a separate link (?rate=P01) once the profile is
# approved. One row per participant; answers are merged in screen by screen, so a participant can resume.
ratings = Table(
    "ratings", metadata,
    Column("session_id", String(40), primary_key=True),
    Column("instrument", String(40), nullable=False),
    Column("doc_order", String(20), nullable=False),          # "profile-first" | "cv-first"
    Column("profile_id", Integer, nullable=False),             # the approved profile that was shown
    Column("status", String(20), nullable=False),              # "in_progress" | "complete"
    Column("answers", JSON, nullable=False),
    Column("screens", JSON, nullable=False),                   # screen id -> {"seconds": .., "at": ..}
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


# review-v1: recruiter study (DO1). Favourites mark the sessions recruiters may see; invites are single-use links;
# one review row per recruiter and candidate; email signups are stored apart from all study data.
favorites = Table(
    "favorites", metadata,
    Column("session_id", String(40), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

# Automatic starring: one row per session the rule has looked at, so a session is judged once and a star you
# remove by hand is never put back. "__baseline__" marks when the rule was switched on.
auto_stars = Table(
    "auto_stars", metadata,
    Column("session_id", String(40), primary_key=True),
    Column("starred", Integer, nullable=False),            # 1 = starred by the rule, 0 = did not qualify, -1 = existing
    Column("reason", Text),
    Column("decided_at", DateTime(timezone=True), nullable=False),
)

recruiters = Table(
    "recruiters", metadata,
    Column("code", String(10), primary_key=True),             # R01, R02, ...
    Column("key", String(40), nullable=False),                 # secret part of the personal link
    Column("invited_by", String(20)),                          # "researcher" or a recruiter code
    Column("consent", JSON, nullable=False),
    Column("guide_done", Integer, default=0),
    Column("experience", String(10)),
    Column("domain", Text),
    Column("comment", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

invites = Table(
    "invites", metadata,
    Column("token", String(40), primary_key=True),
    Column("created_by", String(20), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("used_by", String(10)),
    Column("used_at", DateTime(timezone=True)),
    Column("revoked", Integer, default=0),
)

reviews = Table(
    "reviews", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("recruiter", String(10), nullable=False, index=True),
    Column("session_id", String(40), nullable=False, index=True),
    Column("profile_id", Integer, nullable=False),
    Column("doc_order", String(20), nullable=False),           # "profile-first" | "cv-first"
    Column("status", String(20), nullable=False),              # "in_progress" | "complete" | "skipped"
    Column("answers", JSON, nullable=False),
    Column("screens", JSON, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("recruiter", "session_id", name="uq_recruiter_session"),
)

signups = Table(   # deliberately no recruiter code and only the date, so emails cannot be linked to ratings
    "signups", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("email", String(200), nullable=False),
    Column("consent_version", String(40), nullable=False),
    Column("signup_date", String(10), nullable=False),
)


class InviteError(RuntimeError):
    """The invite link is unknown, used, revoked or expired."""


class ConflictError(RuntimeError):
    """The session changed since it was loaded (second tab or double submit)."""


def _now():
    return datetime.now(timezone.utc)


class Store:
    def __init__(self, url: str):
        kwargs = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        self.engine = create_engine(url, **kwargs)
        event.listen(self.engine, "after_cursor_execute", _count_writes)
        metadata.create_all(self.engine)

    # ---- sessions
    def get_session(self, sid: str) -> dict | None:
        with self.engine.connect() as c:
            row = c.execute(select(sessions).where(sessions.c.id == sid)).mappings().first()
        return dict(row) if row else None

    def list_sessions(self) -> list[dict]:
        with self.engine.connect() as c:
            rows = c.execute(select(sessions).order_by(sessions.c.created_at.desc())).mappings().all()
        return [dict(r) for r in rows]

    def create_session(self, sid: str, state: dict, first_turn: dict) -> None:
        now = _now()
        with self.engine.begin() as c:
            c.execute(insert(sessions).values(id=sid, status=state["status"], version=1, state=state,
                                              consent_at=now, created_at=now, updated_at=now))
            c.execute(insert(turns).values(session_id=sid, created_at=now, **first_turn))

    def save_turn(self, sid: str, expected_version: int, state: dict, turn_row: dict) -> int:
        now = _now()
        with self.engine.begin() as c:
            r = c.execute(update(sessions)
                          .where(sessions.c.id == sid, sessions.c.version == expected_version)
                          .values(state=state, status=state["status"], version=expected_version + 1,
                                  updated_at=now))
            if r.rowcount != 1:
                raise ConflictError(sid)
            c.execute(insert(turns).values(session_id=sid, created_at=now, **turn_row))
        return expected_version + 1

    # ---- turns
    def get_turns(self, sid: str) -> list[dict]:
        with self.engine.connect() as c:
            rows = c.execute(select(turns).where(turns.c.session_id == sid).order_by(turns.c.turn)).mappings().all()
        return [dict(r) for r in rows]

    def set_audit(self, turn_id: int, audit: str, note: str) -> None:
        with self.engine.begin() as c:
            c.execute(update(turns).where(turns.c.id == turn_id).values(audit=audit, audit_note=note))

    # ---- anonymised CV (the original upload is never stored)
    def save_cv(self, sid: str, pdf: bytes, report: dict) -> None:
        now = _now()
        with self.engine.begin() as c:
            c.execute(cvs.delete().where(cvs.c.session_id == sid))
            c.execute(insert(cvs).values(session_id=sid, pdf=pdf, report=report, checked=0, created_at=now))

    def get_cv(self, sid: str) -> dict | None:
        with self.engine.connect() as c:
            row = c.execute(select(cvs).where(cvs.c.session_id == sid)).mappings().first()
        return dict(row) if row else None

    def mark_cv_checked(self, sid: str, checked: bool) -> None:
        with self.engine.begin() as c:
            c.execute(update(cvs).where(cvs.c.session_id == sid).values(checked=int(checked)))

    # ---- redaction rules and profiles
    def save_redaction(self, sid: str, rules: str) -> None:
        with self.engine.begin() as c:
            c.execute(redactions.delete().where(redactions.c.session_id == sid))
            c.execute(insert(redactions).values(session_id=sid, rules=rules, confirmed_at=_now()))

    def get_redaction(self, sid: str) -> dict | None:
        with self.engine.connect() as c:
            row = c.execute(select(redactions).where(redactions.c.session_id == sid)).mappings().first()
        return dict(row) if row else None

    def save_merges(self, sid: str, pairs: list[list[str]]) -> None:
        with self.engine.begin() as c:
            c.execute(merges.delete().where(merges.c.session_id == sid))
            c.execute(insert(merges).values(session_id=sid, pairs=[sorted(p) for p in pairs], confirmed_at=_now()))

    def get_merges(self, sid: str) -> dict | None:
        with self.engine.connect() as c:
            row = c.execute(select(merges).where(merges.c.session_id == sid)).mappings().first()
        return dict(row) if row else None

    def add_profile(self, sid: str, version: str, ok: bool, reason: str, profile: dict, log: dict,
                    pdf: bytes | None) -> int:
        with self.engine.begin() as c:
            r = c.execute(insert(profiles).values(session_id=sid, version=version, ok=int(ok), reason=reason,
                                                  profile=profile, log=log, pdf=pdf, approved=0, created_at=_now()))
            return int(r.inserted_primary_key[0])

    def list_profiles(self, sid: str) -> list[dict]:
        with self.engine.connect() as c:
            rows = c.execute(select(profiles).where(profiles.c.session_id == sid)
                             .order_by(profiles.c.id.desc())).mappings().all()
        return [dict(r) for r in rows]

    def approve_profile(self, pid: int, approved: bool) -> None:
        with self.engine.begin() as c:
            c.execute(update(profiles).where(profiles.c.id == pid).values(approved=int(approved)))

    def get_approved_profile(self, sid: str) -> dict | None:
        """Newest approved profile that has a PDF (the one a candidate rates and recruiters receive)."""
        with self.engine.connect() as c:
            row = c.execute(select(profiles).where(profiles.c.session_id == sid, profiles.c.approved == 1,
                                                   profiles.c.ok == 1, profiles.c.pdf.is_not(None))
                            .order_by(profiles.c.id.desc())).mappings().first()
        return dict(row) if row else None

    # ---- lightweight bulk reads (no PDFs, no logs): one query each, for overviews and the recruiter queue
    def profiles_meta(self) -> dict[str, list[dict]]:
        """session_id -> profiles (id, version, ok, approved, has_pdf), newest first."""
        cols = (profiles.c.id, profiles.c.session_id, profiles.c.version, profiles.c.ok, profiles.c.approved,
                profiles.c.created_at, profiles.c.pdf.is_not(None).label("has_pdf"))
        out: dict[str, list[dict]] = {}
        with self.engine.connect() as c:
            for r in c.execute(select(*cols).order_by(profiles.c.id.desc())).mappings():
                out.setdefault(r["session_id"], []).append(dict(r))
        return out

    def cvs_meta(self) -> dict[str, dict]:
        """session_id -> {"checked": bool} for every stored CV (without the PDF)."""
        with self.engine.connect() as c:
            return {r[0]: {"checked": bool(r[1])} for r in c.execute(select(cvs.c.session_id, cvs.c.checked))}

    def get_profile(self, pid: int) -> dict | None:
        with self.engine.connect() as c:
            row = c.execute(select(profiles).where(profiles.c.id == pid)).mappings().first()
        return dict(row) if row else None

    # ---- candidate questionnaire
    def get_rating(self, sid: str) -> dict | None:
        with self.engine.connect() as c:
            row = c.execute(select(ratings).where(ratings.c.session_id == sid)).mappings().first()
        return dict(row) if row else None

    def list_ratings(self) -> list[dict]:
        with self.engine.connect() as c:
            rows = c.execute(select(ratings).order_by(ratings.c.session_id)).mappings().all()
        return [dict(r) for r in rows]

    def start_rating(self, sid: str, instrument: str, doc_order: str, profile_id: int) -> dict:
        """Creates the row once; a second call (reload, second tab) returns the existing row unchanged."""
        existing = self.get_rating(sid)
        if existing:
            return existing
        now = _now()
        try:
            with self.engine.begin() as c:
                c.execute(insert(ratings).values(session_id=sid, instrument=instrument, doc_order=doc_order,
                                                 profile_id=profile_id, status="in_progress", answers={},
                                                 screens={}, started_at=now, updated_at=now))
        except Exception:  # created concurrently
            pass
        return self.get_rating(sid)

    def save_rating_screen(self, sid: str, screen: str, answers: dict, seconds: float | None,
                           complete: bool = False) -> None:
        """Merges one screen's answers. Ignored once the questionnaire is complete (answers are then locked)."""
        now = _now()
        with self.engine.begin() as c:
            row = c.execute(select(ratings).where(ratings.c.session_id == sid)).mappings().first()
            if row is None or row["status"] == "complete":
                return
            merged = {**(row["answers"] or {}), **answers}
            screens = dict(row["screens"] or {})
            prev = (screens.get(screen) or {}).get("seconds") or 0
            screens[screen] = {"seconds": round(prev + (seconds or 0), 1), "at": now.isoformat()}
            values = {"answers": merged, "screens": screens, "updated_at": now}
            if complete:
                values.update(status="complete", completed_at=now)
            c.execute(update(ratings).where(ratings.c.session_id == sid).values(**values))

    def repin_rating(self, sid: str, profile_id: int) -> None:
        """Switches to a newer approved profile, only while no answer has been saved yet."""
        with self.engine.begin() as c:
            row = c.execute(select(ratings).where(ratings.c.session_id == sid)).mappings().first()
            if row and not row["answers"]:
                c.execute(update(ratings).where(ratings.c.session_id == sid)
                          .values(profile_id=profile_id, updated_at=_now()))

    def delete_rating(self, sid: str) -> None:
        with self.engine.begin() as c:
            c.execute(ratings.delete().where(ratings.c.session_id == sid))

    # ---- favourites
    def set_favorite(self, sid: str, on: bool) -> None:
        with self.engine.begin() as c:
            c.execute(favorites.delete().where(favorites.c.session_id == sid))
            if on:
                c.execute(insert(favorites).values(session_id=sid, created_at=_now()))

    def list_favorites(self) -> set[str]:
        with self.engine.connect() as c:
            return {r[0] for r in c.execute(select(favorites.c.session_id))}

    def list_auto_decisions(self) -> dict[str, dict]:
        with self.engine.connect() as c:
            return {r["session_id"]: dict(r) for r in c.execute(select(auto_stars)).mappings()}

    def record_auto_decision(self, sid: str, starred: int, reason: str) -> None:
        with self.engine.begin() as c:
            c.execute(auto_stars.delete().where(auto_stars.c.session_id == sid))
            c.execute(insert(auto_stars).values(session_id=sid, starred=starred, reason=reason, decided_at=_now()))

    # ---- invites and recruiters
    def create_invite(self, created_by: str, days: int = 14) -> str:
        import secrets
        token = "inv-" + secrets.token_urlsafe(6)
        now = _now()
        with self.engine.begin() as c:
            c.execute(insert(invites).values(token=token, created_by=created_by, created_at=now,
                                             expires_at=now + timedelta(days=days), revoked=0))
        return token

    def get_invite(self, token: str) -> dict | None:
        with self.engine.connect() as c:
            row = c.execute(select(invites).where(invites.c.token == token)).mappings().first()
        return dict(row) if row else None

    def list_invites(self) -> list[dict]:
        with self.engine.connect() as c:
            return [dict(r) for r in c.execute(select(invites).order_by(invites.c.created_at.desc())).mappings()]

    def revoke_invite(self, token: str) -> None:
        with self.engine.begin() as c:
            c.execute(update(invites).where(invites.c.token == token).values(revoked=1))

    @staticmethod
    def invite_problem(inv: dict | None) -> str | None:
        if inv is None:
            return "unknown"
        if inv["revoked"]:
            return "revoked"
        if inv["used_by"]:
            return "used"
        exp = inv["expires_at"]
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < _now():
            return "expired"
        return None

    def redeem_invite(self, token: str, consent: dict) -> dict:
        """Creates the next recruiter (R01, R02, ...) and marks the invite used, in one transaction."""
        import secrets
        for _ in range(5):
            try:
                with self.engine.begin() as c:
                    inv = c.execute(select(invites).where(invites.c.token == token)).mappings().first()
                    problem = self.invite_problem(dict(inv) if inv else None)
                    if problem:
                        raise InviteError(problem)
                    n = len(c.execute(select(recruiters.c.code)).all()) + 1
                    code, now = f"R{n:02d}", _now()
                    c.execute(insert(recruiters).values(code=code, key=secrets.token_urlsafe(8),
                                                        invited_by=inv["created_by"], consent=consent,
                                                        guide_done=0, created_at=now))
                    r = c.execute(update(invites).where(invites.c.token == token, invites.c.used_by.is_(None))
                                  .values(used_by=code, used_at=now))
                    if r.rowcount != 1:
                        raise InviteError("used")
                return self.get_recruiter(code)
            except InviteError:
                raise
            except Exception:   # two recruiters got the same next code at once: try again
                continue
        raise RuntimeError("could not create a recruiter code")

    def get_recruiter(self, code: str) -> dict | None:
        with self.engine.connect() as c:
            row = c.execute(select(recruiters).where(recruiters.c.code == code)).mappings().first()
        return dict(row) if row else None

    def list_recruiters(self) -> list[dict]:
        with self.engine.connect() as c:
            return [dict(r) for r in c.execute(select(recruiters).order_by(recruiters.c.code)).mappings()]

    def update_recruiter(self, code: str, **values) -> None:
        with self.engine.begin() as c:
            c.execute(update(recruiters).where(recruiters.c.code == code).values(**values))

    # ---- reviews
    def list_reviews(self, recruiter: str | None = None, session_id: str | None = None) -> list[dict]:
        q = select(reviews)
        if recruiter:
            q = q.where(reviews.c.recruiter == recruiter)
        if session_id:
            q = q.where(reviews.c.session_id == session_id)
        with self.engine.connect() as c:
            return [dict(r) for r in c.execute(q.order_by(reviews.c.id)).mappings()]

    def get_review(self, rid: int) -> dict | None:
        with self.engine.connect() as c:
            row = c.execute(select(reviews).where(reviews.c.id == rid)).mappings().first()
        return dict(row) if row else None

    def create_review(self, recruiter: str, session_id: str, profile_id: int, doc_order: str) -> dict | None:
        """Returns the new review, or None if this recruiter already has one for this candidate."""
        now = _now()
        try:
            with self.engine.begin() as c:
                r = c.execute(insert(reviews).values(recruiter=recruiter, session_id=session_id, profile_id=profile_id,
                                                     doc_order=doc_order, status="in_progress", answers={},
                                                     screens={}, started_at=now, updated_at=now))
                rid = int(r.inserted_primary_key[0])
        except Exception:
            return None
        return self.get_review(rid)

    def save_review_screen(self, rid: int, screen: str, answers: dict, seconds: float | None,
                           complete: bool = False) -> None:
        now = _now()
        with self.engine.begin() as c:
            row = c.execute(select(reviews).where(reviews.c.id == rid)).mappings().first()
            if row is None or row["status"] != "in_progress":
                return
            screens = dict(row["screens"] or {})
            prev = (screens.get(screen) or {}).get("seconds") or 0
            screens[screen] = {"seconds": round(prev + (seconds or 0), 1), "at": now.isoformat()}
            values = {"answers": {**(row["answers"] or {}), **answers}, "screens": screens, "updated_at": now}
            if complete:
                values.update(status="complete", completed_at=now)
            c.execute(update(reviews).where(reviews.c.id == rid).values(**values))

    def skip_review(self, rid: int) -> None:
        with self.engine.begin() as c:
            c.execute(update(reviews).where(reviews.c.id == rid, reviews.c.status == "in_progress")
                      .values(status="skipped", updated_at=_now()))

    def delete_reviews(self, recruiter: str) -> None:
        with self.engine.begin() as c:
            c.execute(reviews.delete().where(reviews.c.recruiter == recruiter))

    # ---- email signups (separate from the study data)
    def add_signup(self, email: str, consent_version: str) -> None:
        with self.engine.begin() as c:
            c.execute(insert(signups).values(email=email.strip(), consent_version=consent_version,
                                             signup_date=_now().date().isoformat()))

    def list_signups(self) -> list[dict]:
        with self.engine.connect() as c:
            return [dict(r) for r in c.execute(select(signups).order_by(signups.c.id)).mappings()]

    def delete_signup(self, sid: int) -> None:
        with self.engine.begin() as c:
            c.execute(signups.delete().where(signups.c.id == sid))

    # ---- export
    def export_session(self, sid: str) -> str:
        sess = self.get_session(sid)
        rows = self.get_turns(sid)
        def ser(v):
            return v.isoformat() if isinstance(v, datetime) else v
        return json.dumps({
            "session": {k: ser(v) for k, v in (sess or {}).items()},
            "turns": [{k: ser(v) for k, v in r.items()} for r in rows],
        }, ensure_ascii=False, indent=2)


# Process-wide reuse for the researcher view: entries are valid until ANY write happens in this process (counted
# by a database hook below) or until SHARED_TTL seconds have passed, whichever comes first.
_SHARED: dict = {}
_GENERATION = [0]
SHARED_TTL = 30


def _count_writes(conn, cursor, statement, parameters, context, executemany):
    if not statement.lstrip()[:6].upper() in ("SELECT", "PRAGMA"):
        _GENERATION[0] += 1
    trace = os.environ.get("SQL_TRACE_FILE")
    if trace:
        with open(trace, "a", encoding="utf-8") as f:
            f.write(statement.split()[0] + " " + (statement.split("FROM")[1].split()[0] if "FROM" in statement else "")
                    + "\n")


def clear_shared() -> None:
    _SHARED.clear()


class RunCache:
    """Wraps a Store for one page run: identical reads are answered once and reused within the run; any write
    clears the reuse, so later reads in the same run see the change. A new RunCache is made for every run, so
    nothing is ever stale across clicks. Returned objects are deep copies, so callers can modify them safely."""

    READ_PREFIXES = ("get_", "list_", "export_")
    READ_NAMES = {"profiles_meta", "cvs_meta"}

    def __init__(self, store: "Store", shared_ttl: float | None = None):
        self._store = store
        self._memo: dict = {}
        self._ttl = shared_ttl
        self._url = str(store.engine.url)

    def __getattr__(self, name):
        attr = getattr(self._store, name)
        if not callable(attr) or name == "invite_problem":
            return attr
        if name.startswith(self.READ_PREFIXES) or name in self.READ_NAMES:
            def read(*args, **kwargs):
                import copy
                key = (name, args, tuple(sorted(kwargs.items())))
                if key not in self._memo:
                    hit = _SHARED.get((self._url, key)) if self._ttl else None
                    if hit and hit[0] == _GENERATION[0] and time.monotonic() - hit[1] < self._ttl:
                        self._memo[key] = hit[2]
                    else:
                        gen = _GENERATION[0]
                        self._memo[key] = attr(*args, **kwargs)
                        if self._ttl:
                            _SHARED[(self._url, key)] = (gen, time.monotonic(), self._memo[key])
                return copy.deepcopy(self._memo[key])
            return read

        def write(*args, **kwargs):
            self._memo.clear()
            return attr(*args, **kwargs)
        return write
