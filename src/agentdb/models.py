"""Typed records returned by :class:`agentdb.AgentDB`.

Every read method hands back one of these dataclasses (or a list of them)
rather than a raw row, so callers get attribute access and a stable shape.
``from_row`` adapts a ``sqlite3.Row`` and, for context entries, decodes the
JSON ``content`` blob back into a Python object.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

LearningType = Literal["failure", "pattern", "gotcha", "preference"]
ContextType = Literal["contract", "checkpoint", "handoff", "verdict"]
Visibility = Literal["agent", "human_only", "operational"]
Sensitivity = Literal["low", "medium", "high"]


@dataclass(slots=True)
class Learning:
    """A durable lesson that survives across sessions."""

    id: str
    ts: str
    type: LearningType
    insight: str
    evidence: str | None = None
    domain: str | None = None
    hit_count: int = 0
    last_hit: str | None = None
    visibility: Visibility = "agent"
    sensitivity: Sensitivity = "low"

    @classmethod
    def from_row(cls, row: Any) -> "Learning":
        return cls(
            id=row["id"],
            ts=row["ts"],
            type=row["type"],
            insight=row["insight"],
            evidence=row["evidence"],
            domain=row["domain"],
            hit_count=row["hit_count"],
            last_hit=row["last_hit"],
            visibility=row["visibility"],
            sensitivity=row["sensitivity"],
        )


@dataclass(slots=True)
class ContextEntry:
    """A piece of ephemeral work state: contract, checkpoint, handoff, or verdict."""

    id: str
    ts: str
    type: ContextType
    content: Any
    contract_id: str | None = None
    agent: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "ContextEntry":
        raw = row["content"]
        try:
            content = json.loads(raw) if raw is not None else None
        except (json.JSONDecodeError, TypeError):
            content = raw
        return cls(
            id=row["id"],
            ts=row["ts"],
            type=row["type"],
            content=content,
            contract_id=row["contract_id"],
            agent=row["agent"],
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

    @classmethod
    def from_row(cls, row: Any) -> "ErrorRecord":
        return cls(
            id=row["id"],
            ts=row["ts"],
            tool=row["tool"],
            error=row["error"],
            file=row["file"],
            context=row["context"],
            domain=row["domain"],
        )


@dataclass(slots=True)
class StartBrief:
    """The digest returned by :meth:`AgentDB.read_start` — what to know before working."""

    learnings: list[Learning] = field(default_factory=list)
    open_contracts: list[ContextEntry] = field(default_factory=list)
    last_checkpoint: ContextEntry | None = None
    recent_errors: list[ErrorRecord] = field(default_factory=list)
