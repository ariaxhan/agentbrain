"""Database schema for agentbrain.

Seven tables, and they exist for one reason: **using the API correctly fills
all of them as a side effect.** That is the whole pitch. A bash hook could
define these tables but never *guarantee* they filled; a library that owns the
write path can.

- ``sessions``     the spine. Every write inherits a ``session_id``. Opening a
  session writes a row; closing it records the outcome.
- ``events``       append-only telemetry. Every write method emits one, so the
  event log is a complete, automatic trace of what the agent did.
- ``learnings``    cross-session memory. ``preference`` rows are special: they
  are *graduated* — proven by evidence, not merely asserted.
- ``context``      work state: ``unit`` (a spec/contract), plus the
  ``checkpoint`` / ``handoff`` / ``verdict`` entries that hang off it.
- ``hypotheses``   the self-improving loop, stage 1. A ``pattern`` learning that
  recurs enough times graduates into a hypothesis under test.
- ``experiments``  the loop, stage 2. Every verdict on a unit testing a
  hypothesis becomes a supporting or refuting experiment.
- ``errors``       captured failures, for after-the-fact diagnosis.

The loop ``learnings → hypotheses → experiments → learnings(preference)`` is the
thing that separates agentbrain from a plain memory store: it does not just
remember what you told it, it discovers what actually works and promotes it.

Types are validated against a *default* vocabulary but are **open** — live data
from the bash era already contained out-of-constraint types, so the columns use
no closed ``CHECK`` set. Callers extend the vocabulary without forking.

The pragmas matter for multi-agent use: WAL lets readers and a writer work
concurrently, and ``busy_timeout`` keeps parallel agents from failing on a
transient lock instead of waiting it out.
"""

from __future__ import annotations

import sqlite3


class IncompatibleDatabaseError(RuntimeError):
    """Raised when a file already contains a table this package needs, but with a
    shape it cannot use or migrate (e.g. a database created by a different tool).

    Refusing up front beats failing on the first write with a cryptic
    ``OperationalError`` half-way through a session.
    """


SCHEMA_VERSION = 2

_PRAGMAS = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;
"""

# Note: no closed CHECK constraints on `type` columns. Live data already
# violated them (`design_decision`, `design`); the package validates against a
# default vocabulary in Python and accepts caller-defined types.
_TABLES = """
-- The spine. No write happens outside a session; opening one writes this row.
CREATE TABLE IF NOT EXISTS sessions (
  id          TEXT PRIMARY KEY,
  started_at  TEXT NOT NULL,
  ended_at    TEXT,
  task        TEXT,
  tier        INTEGER,
  agent       TEXT,
  success     INTEGER,              -- 1 / 0 / NULL (unknown)
  outcome     TEXT,
  tokens_used INTEGER,
  meta        TEXT                  -- JSON
);

-- Append-only telemetry. Every write method emits one automatically.
CREATE TABLE IF NOT EXISTS events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ts         TEXT NOT NULL,
  session_id TEXT,
  kind       TEXT NOT NULL,         -- session|learn|recall|unit|checkpoint|handoff|verdict|error|graduate|experiment
  ref_id     TEXT,                  -- id of the row this event is about
  data       TEXT                   -- JSON
);

-- Cross-session memory. `preference` rows are graduated (proven by evidence).
CREATE TABLE IF NOT EXISTS learnings (
  id            TEXT PRIMARY KEY,
  ts            TEXT NOT NULL,
  session_id    TEXT,
  type          TEXT NOT NULL,      -- default vocab: failure|pattern|gotcha|preference
  insight       TEXT NOT NULL,
  evidence      TEXT,
  domain        TEXT,
  hit_count     INTEGER NOT NULL DEFAULT 1,
  last_hit      TEXT,
  hypothesis_id TEXT,               -- set when this pattern graduated to a hypothesis
  visibility    TEXT NOT NULL DEFAULT 'agent',
  sensitivity   TEXT NOT NULL DEFAULT 'low'
);

-- Work state: units (specs/contracts) and the entries that hang off them.
-- `contract_id` keeps its historical name for read-compatibility with the
-- bash-era databases; the API surfaces it as `unit_id`.
CREATE TABLE IF NOT EXISTS context (
  id            TEXT PRIMARY KEY,
  ts            TEXT NOT NULL,
  session_id    TEXT,
  type          TEXT NOT NULL,      -- default vocab: unit|checkpoint|handoff|verdict
  kind          TEXT,               -- for units: 'spec' | 'contract'
  contract_id   TEXT,               -- the work unit this hangs off (a.k.a. unit_id)
  hypothesis_id TEXT,               -- a unit may test a hypothesis
  acceptance    TEXT,               -- JSON list; required when kind='spec'
  agent         TEXT,
  content       TEXT NOT NULL       -- JSON blob or plain text
);

-- The loop, stage 1: recurring patterns become hypotheses under test.
CREATE TABLE IF NOT EXISTS hypotheses (
  id                    TEXT PRIMARY KEY,
  ts                    TEXT NOT NULL,
  session_id            TEXT,
  statement             TEXT NOT NULL,
  domain                TEXT,
  source_learning_id    TEXT,
  status                TEXT NOT NULL DEFAULT 'testing',  -- testing|graduated|rejected
  confidence            REAL NOT NULL DEFAULT 0.0,
  evidence_for          INTEGER NOT NULL DEFAULT 0,
  evidence_against      INTEGER NOT NULL DEFAULT 0,
  graduated_learning_id TEXT
);

-- The loop, stage 2: a verdict on a hypothesis is an experiment.
CREATE TABLE IF NOT EXISTS experiments (
  id            TEXT PRIMARY KEY,
  ts            TEXT NOT NULL,
  session_id    TEXT,
  hypothesis_id TEXT NOT NULL,
  unit_id       TEXT,
  result        TEXT NOT NULL,      -- supports|refutes
  evidence      TEXT
);

-- Captured failures.
CREATE TABLE IF NOT EXISTS errors (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ts         TEXT NOT NULL,
  session_id TEXT,
  tool       TEXT NOT NULL,
  error      TEXT NOT NULL,
  file       TEXT,
  context    TEXT,
  domain     TEXT
);

CREATE TABLE IF NOT EXISTS _migrations (
  version    INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);
"""

# Created *after* the column migration runs, because some index a legacy database
# needs (e.g. on context.contract_id) targets a column only added by migration.
_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_learnings_type     ON learnings(type);
CREATE INDEX IF NOT EXISTS idx_learnings_domain   ON learnings(domain);
CREATE INDEX IF NOT EXISTS idx_context_type       ON context(type);
CREATE INDEX IF NOT EXISTS idx_context_contract   ON context(contract_id);
CREATE INDEX IF NOT EXISTS idx_context_ts         ON context(ts);
CREATE INDEX IF NOT EXISTS idx_events_session     ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_kind        ON events(kind);
CREATE INDEX IF NOT EXISTS idx_hypotheses_status  ON hypotheses(status);
CREATE INDEX IF NOT EXISTS idx_experiments_hyp    ON experiments(hypothesis_id);
CREATE INDEX IF NOT EXISTS idx_errors_ts          ON errors(ts);
"""

# Columns added since the bash-era schema. Applied to pre-existing base tables
# so the package can open and migrate an older database forward in place.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "learnings": {"session_id": "TEXT", "hypothesis_id": "TEXT"},
    "context": {
        "session_id": "TEXT",
        "kind": "TEXT",
        "contract_id": "TEXT",
        "hypothesis_id": "TEXT",
        "acceptance": "TEXT",
        "agent": "TEXT",
    },
    "errors": {"session_id": "TEXT"},
}

# Columns the package writes into and therefore requires. If a table pre-exists
# missing one of these and it can't be added by migration, the file was built by
# something else with an incompatible shape — refuse rather than corrupt it.
_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "sessions": {"id", "started_at", "ended_at", "task", "tier", "agent", "success", "outcome", "tokens_used"},
    "events": {"id", "ts", "session_id", "kind", "ref_id", "data"},
    "learnings": {"id", "ts", "type", "insight", "hit_count", "hypothesis_id"},
    "context": {"id", "ts", "type", "kind", "contract_id", "hypothesis_id", "acceptance", "agent", "content"},
    "hypotheses": {"id", "ts", "statement", "status", "confidence", "evidence_for", "evidence_against", "source_learning_id", "graduated_learning_id"},
    "experiments": {"id", "ts", "hypothesis_id", "unit_id", "result"},
    "errors": {"id", "ts", "tool", "error"},
}


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def apply_schema(conn: sqlite3.Connection, now: str) -> None:
    """Create tables if absent and migrate an older database forward.

    Idempotent: safe to run on a fresh file, a current file, or a bash-era
    database that predates ``sessions``/``events``/``hypotheses``/``experiments``
    and the columns those features added.
    """
    conn.executescript(_PRAGMAS)
    conn.executescript(_TABLES)
    # Bring forward any pre-existing base table that's missing newer columns.
    for table, columns in _ADDED_COLUMNS.items():
        if not _table_exists(conn, table):
            continue
        present = _existing_columns(conn, table)
        for name, decl in columns.items():
            if name not in present:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    # Verify every table the package writes to has the columns it needs. A
    # foreign-shaped pre-existing table (e.g. a bash-era 12-table KERNEL DB whose
    # `events`/`hypotheses`/`experiments` differ) is caught here, not mid-write.
    for table, required in _REQUIRED_COLUMNS.items():
        present = _existing_columns(conn, table)
        missing = required - present
        if missing:
            raise IncompatibleDatabaseError(
                f"table {table!r} exists but is missing columns {sorted(missing)}; "
                "this database was created by another tool with an incompatible "
                "schema and cannot be opened by agentbrain."
            )
    # Indexes last, now that every migrated column exists.
    conn.executescript(_INDEXES)
    conn.execute(
        "INSERT OR IGNORE INTO _migrations (version, applied_at) VALUES (?, ?)",
        (SCHEMA_VERSION, now),
    )
    conn.commit()
