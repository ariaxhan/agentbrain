"""The :class:`AgentDB` memory layer.

A thin, typed wrapper over a single SQLite file. The design goals:

- **Library-first.** You import it and call methods that return objects; there
  is no subprocess and no required CLI.
- **Zero dependencies.** Only the standard-library ``sqlite3``.
- **Safe under concurrency.** WAL mode plus ``busy_timeout`` let multiple agent
  processes share one database file; an in-process lock guards the single
  connection so the same instance is safe to call from multiple threads.
- **Injection-proof.** Every value is bound as a parameter; user strings never
  reach the SQL text.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import (
    ContextEntry,
    ContextType,
    ErrorRecord,
    Learning,
    LearningType,
    Sensitivity,
    StartBrief,
    Visibility,
)
from .schema import SCHEMA

_LEARNING_TYPES = frozenset({"failure", "pattern", "gotcha", "preference"})
_VERDICTS = frozenset({"pass", "fail"})


def _now() -> str:
    """Current time as an ISO-8601 UTC string with a trailing ``Z``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _dump(content: Any) -> str:
    """Serialize a checkpoint/contract payload to JSON.

    Strings pass through untouched so callers can store plain text; everything
    else is JSON-encoded.
    """
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)


class AgentDB:
    """A SQLite-backed memory store for AI agents.

    Open one per project::

        db = AgentDB("agent.db")
        db.learn("gotcha", "WAL needed for concurrent agents", domain="db")
        brief = db.read_start()

    The instance is also a context manager and closes its connection on exit.
    """

    def __init__(self, path: str | Path = "agent.db", *, agent: str | None = None) -> None:
        """Open (creating if needed) the database at ``path``.

        ``agent`` is an optional default label (e.g. ``"surgeon"``) stamped on
        context entries that don't specify their own.
        """
        self.path = ":memory:" if str(path) == ":memory:" else str(Path(path).expanduser())
        self.agent = agent
        self._lock = threading.Lock()
        # check_same_thread=False + our own lock makes one instance usable from
        # several threads; WAL + busy_timeout handle several processes.
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "AgentDB":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- internal helpers --------------------------------------------------

    def _execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    # -- learnings (cross-session memory) ----------------------------------

    def learn(
        self,
        type: LearningType,
        insight: str,
        *,
        evidence: str | None = None,
        domain: str | None = None,
        visibility: Visibility = "agent",
        sensitivity: Sensitivity = "low",
    ) -> Learning:
        """Record a durable lesson. Returns the stored :class:`Learning`."""
        if type not in _LEARNING_TYPES:
            raise ValueError(f"type must be one of {sorted(_LEARNING_TYPES)}, got {type!r}")
        if not insight or not insight.strip():
            raise ValueError("insight must be a non-empty string")
        record = Learning(
            id=_new_id("learn"),
            ts=_now(),
            type=type,
            insight=insight,
            evidence=evidence,
            domain=domain,
            visibility=visibility,
            sensitivity=sensitivity,
        )
        self._execute(
            """INSERT INTO learnings
               (id, ts, type, insight, evidence, domain, visibility, sensitivity)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (record.id, record.ts, record.type, record.insight, record.evidence,
             record.domain, record.visibility, record.sensitivity),
        )
        return record

    def learnings(
        self,
        *,
        type: LearningType | None = None,
        domain: str | None = None,
        limit: int = 50,
    ) -> list[Learning]:
        """Fetch stored learnings, newest first, optionally filtered."""
        clauses: list[str] = []
        params: list[Any] = []
        if type is not None:
            clauses.append("type = ?")
            params.append(type)
        if domain is not None:
            clauses.append("domain = ?")
            params.append(domain)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = self._query(
            f"SELECT * FROM learnings {where} ORDER BY ts DESC LIMIT ?", params
        )
        return [Learning.from_row(r) for r in rows]

    def recall(self, query: str, *, limit: int = 20) -> list[Learning]:
        """Search learnings by substring across insight and evidence.

        Matching rows have their ``hit_count`` bumped and ``last_hit`` stamped,
        so frequently-useful lessons surface their value over time.
        """
        like = f"%{query}%"
        rows = self._query(
            """SELECT * FROM learnings
               WHERE insight LIKE ? OR evidence LIKE ?
               ORDER BY ts DESC LIMIT ?""",
            (like, like, limit),
        )
        results = [Learning.from_row(r) for r in rows]
        if results:
            now = _now()
            ids = [r.id for r in results]
            placeholders = ",".join("?" for _ in ids)
            self._execute(
                f"""UPDATE learnings
                    SET hit_count = hit_count + 1, last_hit = ?
                    WHERE id IN ({placeholders})""",
                [now, *ids],
            )
        return results

    def forget(self, learning_id: str) -> bool:
        """Delete a learning by id. Returns True if a row was removed."""
        cur = self._execute("DELETE FROM learnings WHERE id = ?", (learning_id,))
        return cur.rowcount > 0

    # -- context (ephemeral work state) ------------------------------------

    def _write_context(
        self,
        type: ContextType,
        content: Any,
        *,
        contract_id: str | None = None,
        agent: str | None = None,
    ) -> ContextEntry:
        entry = ContextEntry(
            id=_new_id(type),
            ts=_now(),
            type=type,
            content=content,
            contract_id=contract_id,
            agent=agent if agent is not None else self.agent,
        )
        self._execute(
            """INSERT INTO context (id, ts, type, contract_id, agent, content)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (entry.id, entry.ts, entry.type, entry.contract_id, entry.agent, _dump(content)),
        )
        return entry

    def contract(self, content: Any, *, agent: str | None = None) -> str:
        """Open a unit of work. Returns the contract id used to link entries."""
        entry = self._write_context("contract", content, agent=agent)
        # A contract links to itself, so it shows up when querying its own id.
        self._execute(
            "UPDATE context SET contract_id = ? WHERE id = ?", (entry.id, entry.id)
        )
        return entry.id

    def checkpoint(
        self, content: Any, *, contract: str | None = None, agent: str | None = None
    ) -> ContextEntry:
        """Record progress mid-work."""
        return self._write_context("checkpoint", content, contract_id=contract, agent=agent)

    def handoff(
        self, content: Any, *, contract: str | None = None, agent: str | None = None
    ) -> ContextEntry:
        """Record a handoff brief for the next session or agent."""
        return self._write_context("handoff", content, contract_id=contract, agent=agent)

    def verdict(
        self,
        result: str,
        *,
        evidence: str | None = None,
        contract: str | None = None,
        agent: str | None = None,
    ) -> ContextEntry:
        """Record a QA verdict (``"pass"`` or ``"fail"``) for a unit of work."""
        if result not in _VERDICTS:
            raise ValueError(f"result must be one of {sorted(_VERDICTS)}, got {result!r}")
        payload = {"result": result, "evidence": evidence}
        return self._write_context("verdict", payload, contract_id=contract, agent=agent)

    def context(
        self,
        *,
        type: ContextType | None = None,
        contract: str | None = None,
        limit: int = 50,
    ) -> list[ContextEntry]:
        """Fetch context entries, newest first, optionally filtered."""
        clauses: list[str] = []
        params: list[Any] = []
        if type is not None:
            clauses.append("type = ?")
            params.append(type)
        if contract is not None:
            clauses.append("contract_id = ?")
            params.append(contract)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = self._query(
            f"SELECT * FROM context {where} ORDER BY ts DESC LIMIT ?", params
        )
        return [ContextEntry.from_row(r) for r in rows]

    # -- session lifecycle -------------------------------------------------

    def read_start(self, *, learnings_limit: int = 20) -> StartBrief:
        """Build the "what to know before working" digest.

        Returns recent learnings, any contracts without a recorded verdict,
        the latest checkpoint, and recent errors.
        """
        recent_learnings = self.learnings(limit=learnings_limit)

        contract_rows = self._query(
            """SELECT c.* FROM context c
               WHERE c.type = 'contract'
                 AND NOT EXISTS (
                   SELECT 1 FROM context v
                   WHERE v.type = 'verdict' AND v.contract_id = c.contract_id
                 )
               ORDER BY c.ts DESC""",
        )
        open_contracts = [ContextEntry.from_row(r) for r in contract_rows]

        last_rows = self._query(
            "SELECT * FROM context WHERE type = 'checkpoint' ORDER BY ts DESC LIMIT 1"
        )
        last_checkpoint = ContextEntry.from_row(last_rows[0]) if last_rows else None

        return StartBrief(
            learnings=recent_learnings,
            open_contracts=open_contracts,
            last_checkpoint=last_checkpoint,
            recent_errors=self.errors(limit=5),
        )

    def write_end(self, content: Any, *, contract: str | None = None) -> ContextEntry:
        """Checkpoint before stopping. Shorthand for :meth:`checkpoint`."""
        return self.checkpoint(content, contract=contract)

    # -- errors ------------------------------------------------------------

    def capture_error(
        self,
        tool: str,
        error: str,
        *,
        file: str | None = None,
        context: str | None = None,
        domain: str | None = None,
    ) -> ErrorRecord:
        """Record a failure for later diagnosis."""
        ts = _now()
        cur = self._execute(
            """INSERT INTO errors (ts, tool, error, file, context, domain)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ts, tool, error, file, context, domain),
        )
        return ErrorRecord(
            id=cur.lastrowid or 0,
            ts=ts,
            tool=tool,
            error=error,
            file=file,
            context=context,
            domain=domain,
        )

    def errors(self, *, limit: int = 50) -> list[ErrorRecord]:
        """Fetch captured errors, newest first."""
        rows = self._query("SELECT * FROM errors ORDER BY ts DESC LIMIT ?", (limit,))
        return [ErrorRecord.from_row(r) for r in rows]

    # -- maintenance -------------------------------------------------------

    def prune(self, *, keep: int = 50) -> int:
        """Trim checkpoint history to the most recent ``keep`` entries.

        Contracts, handoffs, verdicts, and learnings are never pruned — only
        the high-churn checkpoint trail. Returns the number deleted.
        """
        cur = self._execute(
            """DELETE FROM context
               WHERE type = 'checkpoint' AND id NOT IN (
                 SELECT id FROM context WHERE type = 'checkpoint'
                 ORDER BY ts DESC LIMIT ?
               )""",
            (keep,),
        )
        return cur.rowcount

    def stats(self) -> dict[str, int]:
        """Row counts per table — a quick health check."""
        out: dict[str, int] = {}
        for table in ("learnings", "context", "errors"):
            rows = self._query(f"SELECT COUNT(*) AS n FROM {table}")
            out[table] = rows[0]["n"]
        return out
