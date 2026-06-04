"""Behavioural tests for AgentDB. Each test proves a contract of the API,
not its implementation, so the schema can evolve underneath them."""

from __future__ import annotations

import threading

import pytest

from agentdb import AgentDB, ContextEntry, Learning


@pytest.fixture()
def db(tmp_path):
    database = AgentDB(tmp_path / "agent.db", agent="tester")
    yield database
    database.close()


# -- learnings -------------------------------------------------------------

def test_learn_returns_stored_record(db):
    rec = db.learn("gotcha", "WAL needed for concurrent agents", domain="db")
    assert isinstance(rec, Learning)
    assert rec.id.startswith("learn_")
    assert rec.type == "gotcha"
    assert rec.domain == "db"


def test_learnings_round_trip_and_filter(db):
    db.learn("failure", "auth token expired silently", domain="auth")
    db.learn("pattern", "retry with backoff", domain="net")
    assert len(db.learnings()) == 2
    assert len(db.learnings(domain="auth")) == 1
    assert len(db.learnings(type="pattern")) == 1


def test_learn_rejects_bad_type(db):
    with pytest.raises(ValueError):
        db.learn("nonsense", "x")  # type: ignore[arg-type]


def test_learn_rejects_empty_insight(db):
    with pytest.raises(ValueError):
        db.learn("pattern", "   ")


def test_recall_searches_and_bumps_hit_count(db):
    db.learn("gotcha", "sqlite locks under concurrent writes")
    db.learn("pattern", "unrelated thing")
    hits = db.recall("concurrent")
    assert len(hits) == 1
    assert hits[0].insight.startswith("sqlite locks")
    # hit_count is now persisted as 1
    again = db.learnings()
    bumped = next(l for l in again if "sqlite" in l.insight)
    assert bumped.hit_count == 1
    assert bumped.last_hit is not None


def test_forget(db):
    rec = db.learn("pattern", "temporary")
    assert db.forget(rec.id) is True
    assert db.forget(rec.id) is False
    assert db.learnings() == []


# -- context ---------------------------------------------------------------

def test_contract_checkpoint_verdict_flow(db):
    cid = db.contract({"goal": "ship auth"})
    assert cid.startswith("contract_")
    cp = db.checkpoint({"did": "wired login"}, contract=cid)
    assert isinstance(cp, ContextEntry)
    assert cp.contract_id == cid
    db.verdict("pass", evidence="12 tests green", contract=cid)
    entries = db.context(contract=cid)
    types = {e.type for e in entries}
    assert types == {"contract", "checkpoint", "verdict"}


def test_checkpoint_preserves_dict_content(db):
    cp = db.checkpoint({"did": "X", "learned": ["Y", "Z"]})
    fetched = db.context(type="checkpoint")[0]
    assert fetched.content == {"did": "X", "learned": ["Y", "Z"]}


def test_checkpoint_preserves_string_content(db):
    db.checkpoint("just a note")
    assert db.context(type="checkpoint")[0].content == "just a note"


def test_default_agent_label_applied(db):
    cp = db.checkpoint({"did": "x"})
    assert cp.agent == "tester"


def test_verdict_rejects_bad_result(db):
    with pytest.raises(ValueError):
        db.verdict("maybe")


# -- session lifecycle -----------------------------------------------------

def test_read_start_brief(db):
    db.learn("pattern", "remembered lesson")
    open_cid = db.contract({"goal": "open work"})
    closed_cid = db.contract({"goal": "done work"})
    db.verdict("pass", contract=closed_cid)
    db.checkpoint({"did": "latest"})

    brief = db.read_start()
    assert any("remembered" in l.insight for l in brief.learnings)
    open_ids = {c.contract_id for c in brief.open_contracts}
    assert open_cid in open_ids
    assert closed_cid not in open_ids
    assert brief.last_checkpoint is not None
    assert brief.last_checkpoint.content == {"did": "latest"}


def test_write_end_is_a_checkpoint(db):
    db.write_end({"did": "final"})
    assert db.context(type="checkpoint")[0].content == {"did": "final"}


# -- errors ----------------------------------------------------------------

def test_capture_and_list_errors(db):
    db.capture_error("Edit", "file not found", file="a.py", domain="fs")
    errs = db.errors()
    assert len(errs) == 1
    assert errs[0].tool == "Edit"
    assert errs[0].file == "a.py"


# -- maintenance -----------------------------------------------------------

def test_prune_keeps_recent_checkpoints_only(db):
    for i in range(10):
        db.checkpoint({"n": i})
    db.contract({"keep": "me"})  # non-checkpoint, must survive
    deleted = db.prune(keep=3)
    assert deleted == 7
    assert len(db.context(type="checkpoint")) == 3
    assert len(db.context(type="contract")) == 1


def test_stats(db):
    db.learn("pattern", "a")
    db.contract({"x": 1})
    db.capture_error("Bash", "boom")
    stats = db.stats()
    assert stats["learnings"] == 1
    assert stats["context"] == 1
    assert stats["errors"] == 1


# -- persistence & concurrency --------------------------------------------

def test_persists_across_reopen(tmp_path):
    path = tmp_path / "p.db"
    with AgentDB(path) as db:
        db.learn("pattern", "durable")
    with AgentDB(path) as db2:
        assert len(db2.learnings()) == 1


def test_thread_safe_writes(tmp_path):
    db = AgentDB(tmp_path / "c.db")

    def worker(n: int) -> None:
        for i in range(20):
            db.learn("pattern", f"thread {n} item {i}")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(db.learnings(limit=1000)) == 80
    db.close()


def test_in_memory_database():
    db = AgentDB(":memory:")
    db.learn("pattern", "ephemeral")
    assert len(db.learnings()) == 1
    db.close()
