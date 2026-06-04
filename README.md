# agent-db

A SQLite-backed memory layer for AI agents. **Read at the start of a session, write at the end.**

Agents forget. Between sessions, between sub-agents, between a crash and a retry, the context that mattered is gone — so the agent repeats the mistake it already made an hour ago. `agent-db` is the small, boring layer that fixes that: one SQLite file, three ideas, zero dependencies.

- **Learnings** — durable lessons that survive forever (`failure`, `pattern`, `gotcha`, `preference`). Read them at the start of every session so an agent stops re-learning the same thing.
- **Context** — ephemeral work state for the current unit of work: a `contract`, the `checkpoint`s along the way, a `handoff` for the next session, and a `verdict` when it's done.
- **Errors** — captured failures, for after-the-fact diagnosis.

It's a **library, not a CLI** — you import it and call methods that return typed objects.

## Install

```bash
pip install agent-db
```

Requires Python 3.10+. No dependencies beyond the standard library.

## Quick start

```python
from agentdb import AgentDB

db = AgentDB("agent.db")

# Durable memory — survives across sessions
db.learn("gotcha", "WAL mode is required for concurrent agents", domain="db")
db.learn("failure", "auth token expired silently", evidence="see log line 412", domain="auth")

# Find a past lesson (bumps its hit count so useful lessons surface)
for lesson in db.recall("concurrent"):
    print(lesson.insight)

# A unit of work: contract -> checkpoint -> verdict
cid = db.contract({"goal": "ship login", "files": ["auth.py"]})
db.checkpoint({"did": "wired the login route"}, contract=cid)
db.verdict("pass", evidence="12 tests green", contract=cid)

# Start of a new session — what do I need to know?
brief = db.read_start()
print(len(brief.learnings), "lessons")
print(len(brief.open_contracts), "unfinished units of work")
print(brief.last_checkpoint)
```

## Why this shape

The philosophy is one line: **read at start, write at end.** Every agent run begins with `read_start()` — recent learnings, unfinished contracts, the last checkpoint — and ends with a `checkpoint()` or `handoff()`. Nothing is lost when the process dies, and a fresh agent can pick up exactly where the last one stopped.

It's built for **multiple agents sharing one file**. SQLite runs in WAL mode with a busy timeout, so several processes can read and write concurrently without stepping on each other; within a process, a single connection is guarded by a lock so one instance is safe to call from multiple threads. Every value is bound as a query parameter — user strings never reach the SQL text.

## API

| Method | What it does |
| --- | --- |
| `learn(type, insight, *, evidence, domain, visibility, sensitivity)` | Record a durable lesson |
| `learnings(*, type, domain, limit)` | Fetch lessons, newest first |
| `recall(query, *, limit)` | Substring-search lessons; bumps hit count |
| `forget(id)` | Delete a lesson |
| `contract(content, *, agent)` | Open a unit of work, returns its id |
| `checkpoint(content, *, contract, agent)` | Record progress mid-work |
| `handoff(content, *, contract, agent)` | Record a brief for the next session |
| `verdict(result, *, evidence, contract, agent)` | Record `"pass"`/`"fail"` |
| `context(*, type, contract, limit)` | Fetch work-state entries |
| `read_start(*, learnings_limit)` | Build the "what to know" digest |
| `write_end(content, *, contract)` | Checkpoint before stopping |
| `capture_error(tool, error, *, file, context, domain)` | Record a failure |
| `errors(*, limit)` | Fetch captured failures |
| `prune(*, keep)` | Trim old checkpoints |
| `stats()` | Row counts per table |

`AgentDB` is also a context manager:

```python
with AgentDB("agent.db") as db:
    db.learn("pattern", "always read_start() first")
```

Use `AgentDB(":memory:")` for an ephemeral in-process store (handy in tests).

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT © Aria Han
