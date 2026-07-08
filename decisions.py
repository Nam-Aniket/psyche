"""Decision journal ledger — the deterministic core of the /decide pipeline.

Same storage pattern and database FILE as the hypothesis ledger in
brainstorm.py (db.resolve_db_path("brainstorm.db")): one file, one backup path.

Status model: rows are 'open' or 'scored'. "Due" is computed (open AND
review_by <= today) by list_due_decisions rather than stored, so no background
job is needed to flip states.

Fail-closed: journal_decision validates every field and raises ValidationError
without writing anything. A journal entry without a scorable prediction would
defeat the journal's purpose (calibration), so prediction/confidence/review_by
are hard requirements.
"""
import json
import sqlite3
from datetime import date, datetime, timezone

import db


class ValidationError(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def _ledger_path():
    return db.resolve_db_path("brainstorm.db")


def _conn(path=None):
    conn = sqlite3.connect(path or _ledger_path())
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            situation TEXT NOT NULL,
            game TEXT NOT NULL,
            game_source TEXT NOT NULL,
            atoms_applied TEXT NOT NULL,
            recommendation TEXT NOT NULL,
            falsifier TEXT NOT NULL,
            prediction TEXT NOT NULL,
            confidence INTEGER NOT NULL,
            review_by TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            outcome TEXT,
            hit TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


_COLS = ["id", "situation", "game", "game_source", "atoms_applied",
         "recommendation", "falsifier", "prediction", "confidence", "review_by",
         "status", "outcome", "hit", "created_at", "updated_at"]


def _get(conn, did):
    r = conn.execute(
        f"SELECT {', '.join(_COLS)} FROM decisions WHERE id=?", (did,)).fetchone()
    if r is None:
        return None
    rec = dict(zip(_COLS, r))
    rec["atoms_applied"] = json.loads(rec["atoms_applied"])
    return rec


def get_decision(path, did):
    conn = _conn(path)
    rec = _get(conn, did)
    conn.close()
    return rec


def list_decisions(path=None, status=None):
    conn = _conn(path)
    q = f"SELECT {', '.join(_COLS)} FROM decisions"
    args = ()
    if status:
        q += " WHERE status = ?"
        args = (status,)
    q += " ORDER BY created_at DESC"
    rows = []
    for r in conn.execute(q, args).fetchall():
        rec = dict(zip(_COLS, r))
        rec["atoms_applied"] = json.loads(rec["atoms_applied"])
        rows.append(rec)
    conn.close()
    return rows


def journal_decision(path=None, *, situation, game, game_source, atoms_applied,
                     recommendation, falsifier, prediction, confidence, review_by):
    """Validated insert. Any invalid field -> ValidationError, no row written."""
    for name, val in [("situation", situation), ("game", game),
                      ("recommendation", recommendation),
                      ("falsifier", falsifier), ("prediction", prediction)]:
        if not (isinstance(val, str) and val.strip()):
            raise ValidationError(f"{name} must be a non-empty string")
    if game_source not in ("atoms", "model-knowledge"):
        raise ValidationError("game_source must be 'atoms' or 'model-knowledge'")
    if not isinstance(atoms_applied, list) or \
            not all(isinstance(a, str) and a.strip() for a in atoms_applied):
        raise ValidationError("atoms_applied must be a list of atom-id strings")
    if game_source == "atoms" and not atoms_applied:
        raise ValidationError("game_source='atoms' requires at least one atom id")
    # bool is an int subclass in Python; confidence=True must not pass as 1
    if isinstance(confidence, bool) or not isinstance(confidence, int) \
            or not (0 <= confidence <= 100):
        raise ValidationError("confidence must be an integer 0-100")
    try:
        date.fromisoformat(review_by)
    except (TypeError, ValueError):
        raise ValidationError("review_by must be an ISO date (YYYY-MM-DD)")

    conn = _conn(path)
    now = _now()
    cur = conn.execute(
        """INSERT INTO decisions
           (situation, game, game_source, atoms_applied, recommendation,
            falsifier, prediction, confidence, review_by, status,
            created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,'open',?,?)""",
        (situation, game, game_source, json.dumps(atoms_applied),
         recommendation, falsifier, prediction, confidence, review_by,
         now, now))
    conn.commit()
    rec = _get(conn, cur.lastrowid)
    conn.close()
    return rec
