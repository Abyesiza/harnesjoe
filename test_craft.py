import asyncio
import json

from core import Briefed, Bus, Node, Task, on
from craft import Expert, render, vague
from mind import Memo, enc
from test_fam import Fake, family, repo

GOOD = {
    "role": "Staff data engineer",
    "goal": "Reliable daily load",
    "verify": ["Row counts reconcile within 0.1%"],
    "unasked": ["Add idempotent re-runs"],
    "alternatives": ["Use dbt incremental models"],
    "rare": ["Great Expectations suites", "made-up gizmo"],
    "start": ["Profile the source with DuckDB"],
    "methods": ["Apply SCD Type 2", "vague thing"],
    "pitfalls": ["Timezone drift in timestamps"],
    "sources": ["Kimball Group, 2013"],
    "fast": "Load one table end to end first.",
}
VAGUE = {**GOOD, "methods": ["do it well"], "rare": ["fancy tool"]}
AUDIT = '{"methods": [0.9, 0.2], "rare": [0.9, 0.1]}'


def expert(draft, audit=AUDIT, **kw):
    bus, seen = Bus(), []

    class Sink(Node):
        @on(Briefed)
        async def b(s, b):
            seen.append(b)

    Sink(bus)
    drafter, auditor = Fake(*[d if isinstance(d, str) else json.dumps(d) for d in draft]), Fake(audit)
    return bus, Expert(bus, drafter, auditor, **kw), drafter, auditor, seen


def run(ex, text):
    return asyncio.run(ex.brief(Task(text)))


def test_vague_detector():
    assert vague("follow best practices") and not vague("Apply SCD Type 2") and not vague("use scikit-learn")


def test_low_confidence_items_are_demoted_not_trusted():
    bus, ex, d, a, seen = expert([GOOD])
    b = run(ex, "load sales data daily")
    assert b["methods"] == ["Apply SCD Type 2"] and b["rare"] == ["Great Expectations suites"]
    assert set(b["unverified"]) == {"vague thing", "made-up gizmo"} and not seen[0].cached
    assert "[caution] unverified: vague thing; made-up gizmo" in render(b)


def test_generic_draft_is_sent_back_once():
    bus, ex, d, a, seen = expert([VAGUE, GOOD])
    run(ex, "load sales data daily")
    assert d.n == 2 and "too generic" in d.seen[1] and a.n == 1


def test_similar_task_reuses_the_brief_and_a_different_one_does_not():
    bus, ex, d, a, seen = expert([GOOD])
    run(ex, "optimize the snowflake query for the daily revenue report")
    run(ex, "optimize snowflake queries for daily revenue reports")
    assert d.n == 1 and [s.cached for s in seen] == [False, True]
    run(ex, "write a poem about autumn")
    assert d.n == 2


def test_unusable_draft_degrades_to_no_brief_and_is_not_cached():
    bus, ex, d, a, seen = expert(["garbage"])
    assert run(ex, "anything") == {} and ex.memo.i == 0 and not seen


def test_short_audit_demotes_unaudited_items():
    bus, ex, d, a, seen = expert([GOOD], audit='{"methods": [], "rare": []}')
    b = run(ex, "load sales data daily")
    assert b["methods"] == [] and b["rare"] == [] and len(b["unverified"]) == 4


def test_brief_reaches_the_implementer_and_the_reviewers(tmp_path):
    bus = Bus()
    drafter, auditor = Fake(json.dumps(GOOD)), Fake(AUDIT)
    impl = Fake("done")
    brain, seen, panel = family(bus, [.9], tmp_path, {"a": impl}, expert=Expert(bus, drafter, auditor))
    asyncio.run(bus.emit(Task("load sales data daily", cwd=repo(tmp_path))))
    assert "Add idempotent re-runs" in impl.seen[0] and "Staff data engineer" in impl.seen[0]
    assert "Row counts reconcile" in panel[0].agent.seen[0] and "Timezone drift" in panel[0].agent.seen[0]


def test_memo_roundtrip_ring_and_persistence(tmp_path):
    m = Memo(n=2, path=str(tmp_path / "m.npz"))
    m.put(enc("alpha beta"), {"v": 1})
    m.put(enc("gamma delta"), {"v": 2})
    m.put(enc("epsilon zeta"), {"v": 3})
    assert m.get(enc("alpha beta"), 0.9) is None and m.get(enc("epsilon zeta"), 0.9) == {"v": 3}
    assert Memo(n=2, path=str(tmp_path / "m.npz")).get(enc("gamma delta"), 0.9) == {"v": 2}
