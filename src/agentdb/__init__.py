"""agent-db — a SQLite-backed memory layer for AI agents.

Read at the start of a session, write at the end. Durable *learnings* that
survive across sessions, ephemeral *context* (contracts, checkpoints,
handoffs, verdicts) for the current unit of work, and captured *errors*.

    from agentdb import AgentDB

    db = AgentDB("agent.db")
    db.learn("gotcha", "WAL mode needed for concurrent agents", domain="db")
    cid = db.contract({"goal": "ship auth"})
    db.checkpoint({"did": "wired login"}, contract=cid)
    db.verdict("pass", evidence="12 tests green", contract=cid)
    brief = db.read_start()
"""

from .db import AgentDB
from .models import (
    ContextEntry,
    ErrorRecord,
    Learning,
    StartBrief,
)

__version__ = "0.1.0"

__all__ = [
    "AgentDB",
    "Learning",
    "ContextEntry",
    "ErrorRecord",
    "StartBrief",
    "__version__",
]
