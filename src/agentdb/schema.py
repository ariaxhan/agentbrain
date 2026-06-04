"""Database schema for agent-db.

Three core tables express the whole concept:

- ``learnings``  cross-session memory that survives forever. Read at the
  start of every session so an agent stops repeating its own mistakes.
- ``context``    ephemeral work state: a ``contract`` (unit of work) and the
  ``checkpoint`` / ``handoff`` / ``verdict`` entries that hang off it.
- ``errors``     captured failures, for after-the-fact diagnosis.

The pragmas matter for multi-agent use: WAL lets readers and a writer work
concurrently, and ``busy_timeout`` keeps parallel agents from failing on a
transient lock instead of waiting it out.
"""

SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;

-- Cross-session memory. Survives forever; read at session start.
CREATE TABLE IF NOT EXISTS learnings (
  id          TEXT PRIMARY KEY,
  ts          TEXT NOT NULL,
  type        TEXT NOT NULL CHECK(type IN ('failure', 'pattern', 'gotcha', 'preference')),
  insight     TEXT NOT NULL,
  evidence    TEXT,
  domain      TEXT,
  hit_count   INTEGER NOT NULL DEFAULT 0,
  last_hit    TEXT,
  visibility  TEXT NOT NULL DEFAULT 'agent',   -- agent | human_only | operational
  sensitivity TEXT NOT NULL DEFAULT 'low'      -- low | medium | high
);

CREATE INDEX IF NOT EXISTS idx_learnings_type   ON learnings(type);
CREATE INDEX IF NOT EXISTS idx_learnings_domain ON learnings(domain);

-- Ephemeral work state, linked into units of work via contract_id.
CREATE TABLE IF NOT EXISTS context (
  id          TEXT PRIMARY KEY,
  ts          TEXT NOT NULL,
  type        TEXT NOT NULL CHECK(type IN ('contract', 'checkpoint', 'handoff', 'verdict')),
  contract_id TEXT,
  agent       TEXT,
  content     TEXT NOT NULL                    -- JSON blob
);

CREATE INDEX IF NOT EXISTS idx_context_type     ON context(type);
CREATE INDEX IF NOT EXISTS idx_context_contract ON context(contract_id);
CREATE INDEX IF NOT EXISTS idx_context_ts       ON context(ts);

-- Captured failures.
CREATE TABLE IF NOT EXISTS errors (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  ts      TEXT NOT NULL,
  tool    TEXT NOT NULL,
  error   TEXT NOT NULL,
  file    TEXT,
  context TEXT,
  domain  TEXT
);

CREATE INDEX IF NOT EXISTS idx_errors_ts ON errors(ts);
"""
