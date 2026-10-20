"""Persistence. SQLite locally, any SQLAlchemy URL (e.g. Supabase Postgres) in production.

Every turn is written in one transaction together with the new session state, guarded by an
optimistic version check, so a double submit or a second browser tab cannot corrupt a session.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import (JSON, Column, DateTime, Integer, LargeBinary, MetaData, String, Table, Text, UniqueConstraint,
                        create_engine, insert, select, update)

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
