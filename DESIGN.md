# agentbrain — Design: every table earns its place

## The problem the bash era couldn't solve

The KERNEL schema defines 12 tables. Across 14 live databases, **only 4 ever held
data** (`events`, `learnings`, `context`, `errors`). The other 8 — the context
graph (`nodes`/`edges`), the experiment engine (`hypotheses`/`experiments`),
`execution_traces`, `context_sessions`, `compaction_events` — sit empty.

They are not empty because they are bad ideas. They are empty because the only thing
populating them was a **bash hook**: fire-and-forget, best-effort, unable to *require*
that an agent record a node, link an edge, or graduate a pattern. The schema was a
good contract with no enforcer.

A Python package is the enforcer. When AgentBrain is a library an agent *calls* rather
than a CLI a hook *hopes will run*, the data structure itself becomes the forcing
function. **This document specifies an API where using it correctly deterministically
populates all 12 tables — bookkeeping as a side effect of ergonomic calls, never a
chore the agent must remember.**

## The principle

> The schema is the contract. The API is shaped so that correct usage *necessarily*
> writes every table. Nothing depends on the caller remembering to log.

Three mechanisms deliver this:

1. **Auto-instrumentation** — every method emits an event and upserts the nodes it
   touches. The caller does nothing extra. (`events`, `nodes`, `context_sessions`)
2. **Projection** — relationships the work already declares become edges. A checkpoint
   that lists two files *is* a co-change edge. (`edges`)
3. **Graduation** — data already flowing crosses a threshold and promotes itself.
   A pattern recalled enough times becomes a hypothesis; a verdict on it becomes an
   experiment. (`hypotheses`, `experiments`, the unused `preference` learning type)

## The spine: `Session`

Everything flows through a session. You cannot `learn`, `checkpoint`, or `trace`
without one open — and opening one writes a `context_sessions` row. This single
constraint is what makes the rest deterministic: every downstream write inherits a
`session_id`, every touched path becomes a node, and the session's success/outcome is
known at close.

```python
from agentbrain import AgentBrain

db = AgentBrain("agent.db")

with db.session(task="feature", tier=2) as s:        # 1 context_sessions row + session event
    s.load("src/auth/login.ts")                       # node(code) upsert + event
    unit = s.unit("add 5s login timeout",             # context(type='spec') + node + event
                  kind="spec",
                  acceptance=["timeout fires at 5s", "existing tests pass"])

    with s.trace("wire timeout", unit=unit) as t:     # execution_traces row written on exit
        # ... agent does the work, recording what it tried ...
        t.plan("set AbortController, 5000ms")
        s.checkpoint({"did": "added timeout"},        # context(checkpoint)
                     files=["src/auth/login.ts",      #   + node upserts
                            "src/auth/api.ts"])        #   + co-change edge login<->api (weight++)
        t.outcome("tests green", success=True)

    s.learn("pattern", "validate token before the API call",  # learning + node + event
            domain="auth")                                     #   hit_count tracked across sessions
    s.verdict("pass", unit=unit, evidence="12 tests green")    # context(verdict)
                                                               #   + experiment if unit tests a hypothesis
# on __exit__: ended_at, success, outcome, tokens_used filled;
#              nodes.avg_success_rate recomputed from this session's result
```

## Table-by-table forcing functions

| Table | Written by | Determinism |
|---|---|---|
| `context_sessions` | `db.session()` context manager — open writes the row, close fills `ended_at`/`success`/`outcome`/`tokens_used` | **Total** — no API call happens outside a session |
| `events` | Every method auto-emits (`session`/`agent`/`hook`/`command`/`error`/`learning`) | **Total** — instrumentation is in the base call path |
| `learnings` | `s.learn(type, insight, …)` | Total (explicit, already the workhorse) |
| `context` | `s.unit()` (contract/spec), `s.checkpoint()`, `s.verdict()`, `s.handoff()` | Total (explicit) |
| `errors` | `s.error(...)` and automatic capture in `trace()`/`session()` `__exit__` on exception | **Total** — exceptions inside a session are recorded, not just hoped-for |
| `nodes` | `s.load(path)` and every `files=[...]` on a checkpoint upsert a node (`access_count++`, `last_accessed`) | **Total** — paths the work names become nodes |
| `edges` | Co-change projection: ≥2 files in one checkpoint → `succeeds_with` edge (weight accrues). Skill/agent loads → `loads`. Learning cross-ref → `references` | **Total** — edges are a projection of declared file/skill relationships |
| `execution_traces` | `with s.trace(goal) as t:` records `exploration`/`plan`/`action`/`outcome`/`success`/`tokens`; `s.unit()` execution auto-opens one | **High** — populated whenever work runs through a trace block (the ergonomic default) |
| `hypotheses` | **Auto-graduation**: when a `pattern` learning's `hit_count` crosses `promote_at` (default 3) it promotes to a hypothesis (`unproven`→`testing`) | **Total** — a trigger on `hit_count`, which `recall()`/`learn()` already move |
| `experiments` | Every `verdict()` referencing a unit that tests a hypothesis becomes an experiment (`supports`/`refutes`) and updates `confidence` + `evidence_for/against` | **Total** — verdicts already happen; they now feed the loop |
| `compaction_events` | `s.handoff()` and resume-gap detection in `read_start()` record before/after token estimates + retention | **High** — written on every handoff and detected resume |

## The self-improving loop (why `preference` was always 0)

The schema always implied a learning lifecycle that the bash era never closed:

```
learn(pattern)  →  recall bumps hit_count  →  hit_count ≥ promote_at
        →  graduate to hypothesis (status: testing)
        →  each verdict on it = experiment (supports / refutes)
        →  confidence updates from evidence_for / evidence_against
        →  confidence ≥ graduate_at  →  hypothesis status: graduated
        →  re-emitted as a learning of type 'preference'  (a proven rule)
```

`preference` had **zero rows** across every database because nothing ever graduated —
there was no mechanism, only a hook that hoped. The package makes graduation a
deterministic consequence of recall + verdict, both of which already flow. AgentBrain
stops being a logbook and becomes a system that *earns* its preferences from evidence.

## The context graph pays off (why `nodes`/`edges` matter)

Once `nodes.avg_success_rate` and `edges.succeeds_with` populate deterministically from
session outcomes, `read_start()` can do more than dump recent rows: it can rank context
by *which files and skills historically correlate with successful sessions* and warn on
`conflicts_with` pairs. The graph that was never filled becomes the thing that makes
context loading smart — but only because the package, not a hook, guarantees it fills.

## Determinism tiers (honest about guarantees)

- **Total** — cannot use the API without writing these: `context_sessions`, `events`,
  `nodes`, `edges`, `learnings`, `context`, `errors`, `hypotheses`, `experiments`.
- **High (ergonomic default)** — written whenever work runs through the natural block:
  `execution_traces` (via `trace()`), `compaction_events` (via `handoff()`/resume).

No table is "aspirational." Every one is either unavoidable or the path of least
resistance.

## Compatibility & extension

- **Read-compatible** with the existing 14 databases — same table and column names,
  same `_migrations` ledger. The package can open and enrich a KERNEL DB in place.
- **Open types, not closed CHECK sets.** Live data already contains out-of-constraint
  types (`design_decision`, `design`). The package validates against a *default* set
  but accepts caller-defined types, so projects extend vocabulary without forking.
- **Project tables** (modelmind's `exercises`, `design_tokens`) attach via a registered
  extension namespace rather than a fork — `db.register_table(sql)` tracked in
  `_migrations`.

## Open questions for review

1. `promote_at` (hit_count → hypothesis) and `graduate_at` (confidence → preference):
   defaults of 3 and 0.8, or tune from the real 5k-learning distribution?
2. Should `unit(kind="spec")` *require* `acceptance=[...]` (enforce the spec/contract
   split at the type level), while `kind="contract"` leaves it optional?
3. `trace()` auto-open on `unit()` execution: on by default, or opt-in?

## v1 scope (shipped)

v1 ships the **self-improving loop** — the thing that makes agentbrain more than a
memory store — and trims the layers that don't earn their place yet. Every table
that ships is filled by the normal flow; nothing is aspirational.

**Shipped (7 tables, all deterministically populated):** `sessions`, `events`,
`learnings` (with graduated `preference` rows), `context` (units / checkpoints /
handoffs / verdicts), `hypotheses`, `experiments`, `errors`. The loop —
`learn(pattern) → hypothesis → experiment → preference` — is the moat and is
fully implemented and tested end-to-end.

**Deferred to a later version (genuinely cuttable, not the differentiator):** the
`nodes`/`edges` context graph, `execution_traces`, and `compaction_events`. These
are good ideas but they are not what sets agentbrain apart from other memory
stores; the loop is. They can land later without changing the core.

**Answers to the three open questions, as built:**
1. Thresholds mined from the real 5,066-learning distribution: `promote_at=3`
   (the recurring tail begins there — 57% of patterns sit at 1–2 hits, then a
   sharp drop), `graduate_at=0.8`. Both are constructor-tunable, with a
   `min_experiments` floor (default 3) so one lucky result can't graduate.
2. Yes — `unit(kind="spec")` requires `acceptance=[...]`; `kind="contract"`
   leaves it optional.
3. The explicit-trace block (`with s.trace(...)`) is deferred along with
   `execution_traces`; exception capture inside a `session()` covers the
   failure-recording case in v1.

**Compatibility, as built:** opens and migrates an older agentbrain / base-schema
database (learnings, context, errors) forward in place. A database whose
`events`/`hypotheses`/`experiments` tables already exist with a *different* shape
(e.g. the bash-era KERNEL 12-table schema) is detected on open and rejected with
`IncompatibleDatabaseError` rather than silently failing on the first write —
those tables' shapes genuinely conflict, so enriching them in place was dropped
in favour of an honest refusal.

**Retrieval, as built:** `recall()` stays substring + `hit_count`, zero-dep. No
embedding search in core; an opt-in `agentbrain[embeddings]` extra is reserved for
later. The moat is the loop, not out-embedding a vector store.
