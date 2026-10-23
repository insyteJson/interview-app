"""Persistence. SQLite locally, any SQLAlchemy URL (e.g. Supabase Postgres) in production.

Every turn is written in one transaction together with the new session state, guarded by an
optimistic version check, so a double submit or a second browser tab cannot corrupt a session.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import (JSON, Column, DateTime, Integer, LargeBinary, MetaData, String, Table, Text, UniqueConstraint,
                        create_engine, insert, select, update)

# Bump whenever a table or a Store method is added: app.py keys its cached Store on this value, so a redeploy
# builds a fresh Store (and creates new tables) instead of reusing one created by the previous code.
SCHEMA_VERSION = "2026-10-23.rating-v1"

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
