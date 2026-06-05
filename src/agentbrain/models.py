"""Typed records returned by :class:`agentbrain.AgentBrain`.

Every read method hands back one of these dataclasses (or a list of them)
rather than a raw row, so callers get attribute access and a stable shape.
``from_row`` adapts a ``sqlite3.Row`` and decodes any JSON payload back into a
Python object.

Type aliases here describe the *default* vocabulary. They are ``Literal`` for
editor help, but the database accepts caller-defined values — the package never
enforces a closed set, because real data already exceeded it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

LearningType = Literal["failure", "pattern", "gotcha", "preference"]
ContextType = Literal["unit", "checkpoint", "handoff", "verdict"]
UnitKind = Literal["spec", "contract"]
HypothesisStatus = Literal["testing", "graduated", "rejected"]
Visibility = Literal["agent", "human_only", "operational"]
Sensitivity = Literal["low", "medium", "high"]


def _decode(raw: Any) -> Any:
    """Best-effort JSON decode; fall back to the raw value (plain text)."""
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


@dataclass(slots=True)
class Learning:
    """A durable lesson. ``type='preference'`` means it was *proven* and graduated."""

    id: str
    ts: str
    type: str
    insight: str
    evidence: str | None = None
    domain: str | None = None
    hit_count: int = 1
    last_hit: str | None = None
    hypothesis_id: str | None = None
    session_id: str | None = None
    visibility: str = "agent"
    sensitivity: str = "low"

    @classmethod
    def from_row(cls, row: Any) -> "Learning":
        keys = row.keys()
        return cls(
            id=row["id"],
            ts=row["ts"],
            type=row["type"],
            insight=row["insight"],
            evidence=row["evidence"],
            domain=row["domain"],
            hit_count=row["hit_count"],
            last_hit=row["last_hit"],
            hypothesis_id=row["hypothesis_id"] if "hypothesis_id" in keys else None,
            session_id=row["session_id"] if "session_id" in keys else None,
            visibility=row["visibility"],
            sensitivity=row["sensitivity"],
        )


@dataclass(slots=True)
class ContextEntry:
    """A piece of work state: a unit (spec/contract), checkpoint, handoff, or verdict."""

    id: str
    ts: str
    type: str
    content: Any
    kind: str | None = None
    unit_id: str | None = None
    hypothesis_id: str | None = None
    acceptance: list[str] | None = None
    agent: str | None = None
    session_id: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "ContextEntry":
        keys = row.keys()
        acc = _decode(row["acceptance"]) if "acceptance" in keys else None
        return cls(
            id=row["id"],
            ts=row["ts"],
            type=row["type"],
            content=_decode(row["content"]),
            kind=row["kind"] if "kind" in keys else None,
            unit_id=row["contract_id"],
            hypothesis_id=row["hypothesis_id"] if "hypothesis_id" in keys else None,
            acceptance=acc if isinstance(acc, list) else None,
            agent=row["agent"],
            session_id=row["session_id"] if "session_id" in keys else None,
        )


@dataclass(slots=True)
class Hypothesis:
    """A pattern under test. Graduates to a ``preference`` learning once proven."""

    id: str
    ts: str
    statement: str
    status: str = "testing"
    confidence: float = 0.0
    evidence_for: int = 0
    evidence_against: int = 0
    domain: str | None = None
    source_learning_id: str | None = None
    graduated_learning_id: str | None = None
    session_id: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "Hypothesis":
        return cls(
            id=row["id"],
            ts=row["ts"],
            statement=row["statement"],
            status=row["status"],
            confidence=row["confidence"],
            evidence_for=row["evidence_for"],
            evidence_against=row["evidence_against"],
            domain=row["domain"],
            source_learning_id=row["source_learning_id"],
            graduated_learning_id=row["graduated_learning_id"],
            session_id=row["session_id"],
        )


@dataclass(slots=True)
class Experiment:
    """A single supporting or refuting result for a hypothesis."""

    id: str
    ts: str
    hypothesis_id: str
    result: str
    unit_id: str | None = None
    evidence: str | None = None
    session_id: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "Experiment":
        return cls(
            id=row["id"],
            ts=row["ts"],
            hypothesis_id=row["hypothesis_id"],
            result=row["result"],
            unit_id=row["unit_id"],
            evidence=row["evidence"],
            session_id=row["session_id"],
        )


@dataclass(slots=True)
class ErrorRecord:
    """A captured failure."""

    id: int
    ts: str
    tool: str
    error: str
    file: str | None = None
    context: str | None = None
    domain: str | None = None
    session_id: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "ErrorRecord":
        keys = row.keys()
        return cls(
            id=row["id"],
            ts=row["ts"],
            tool=row["tool"],
            error=row["error"],
            file=row["file"],
            context=row["context"],
            domain=row["domain"],
            session_id=row["session_id"] if "session_id" in keys else None,
        )


@dataclass(slots=True)
class StartBrief:
    """The digest returned by :meth:`AgentBrain.read_start` — what to know before working.

    ``preferences`` come first by design: they are the rules agentbrain has
    *proven* through the learn → hypothesis → experiment loop, as opposed to
    ``learnings`` (recent, unproven) and ``open_hypotheses`` (still under test).
    """

    preferences: list[Learning] = field(default_factory=list)
    learnings: list[Learning] = field(default_factory=list)
    open_hypotheses: list[Hypothesis] = field(default_factory=list)
    open_units: list[ContextEntry] = field(default_factory=list)
    last_checkpoint: ContextEntry | None = None
    recent_errors: list[ErrorRecord] = field(default_factory=list)
