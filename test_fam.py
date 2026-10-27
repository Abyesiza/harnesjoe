import asyncio
import json
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest

from agents import Agent, ClaudeCode, Persona, Rules
from core import (
    Bus,
    Correction,
    Decision,
    Finding,
    Node,
    ResearchRequest,
    Task,
    Tick,
    Ticker,
    ToolAnswer,
    jsonin,
    on,
)
from family import Gate, Judge, Lead, Panel, Researcher, Suite
from mind import Brain, enc, spread

PE = Persona("T", "m")


class Fake(Agent):
    def __init__(s, *replies):
        s.replies, s.n, s.seen, s.mcp = replies, 0, [], []

    async def ask(s, text, cwd=".", mcp=None):
        s.seen.append(text)
        s.mcp.append(mcp)
        r = s.replies[min(s.n, len(s.replies) - 1)]
        s.n += 1
        return r


class Fixer(Fake):
    async def ask(s, text, cwd=".", mcp=None):
        Path(cwd, "test_x.py").write_text("def test_x():\n    assert True\n")
        return await super().ask(text, cwd, mcp)


def repo(tmp_path, passing=True):
    run = lambda *a: subprocess.run(a, cwd=tmp_path, check=True, capture_output=True)
    run("git", "init", "-q")
    (tmp_path / "test_x.py").write_text(f"def test_x():\n    assert {passing}\n")
    run("git", "add", ".")
    run("git", "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", "i")
    return str(tmp_path)


def family(bus, votes, tmp_path, members=None, root=None, **kw):
    members = members or {"a": Fake("done"), "b": Fake("done")}
    brain = Brain(list(members))
    Lead(bus, members, brain, root or tmp_path / "env", **kw)
    Suite(bus)
    panel = [Panel(bus, f"r{i}", Fake(f'{{"accept": {v}}}')) for i, v in enumerate(votes)]
    Judge(bus, 1 + len(panel))
    seen = []

    class Sink(Node):
        @on(Decision)
        async def d(s, d):
            seen.append(d)

    Sink(bus)
    return brain, seen, panel


def test_spread():
    same, split = spread(np.array([[.1, .9]] * 3)), spread(np.array([[.1, .9], [.9, .1]]))
    assert same < 1e-9 < 0.3 < split <= np.log(2)


def test_jsonin_rejects_prose():
    with pytest.raises(ValueError):
        jsonin("no json here")
    assert jsonin('noise {"a": 1} noise') == {"a": 1}


def test_brain_learns_and_stays_fast():
    b = Brain(["a", "b"])
    for _ in range(100):
        b.update(enc("fix login bug"), "a", 1.0)
        b.update(enc("write docs"), "b", 1.0)
    assert [b.pick(enc("fix login bug")) for _ in range(200)].count("a") > 190
    assert abs(b.mean(enc("never seen words"))[0] - 0.5) < 0.05
    t = time.perf_counter()
    [b.pick(enc("fix login bug")) for _ in range(200)]
    assert (time.perf_counter() - t) / 200 < 0.005


def test_encoder_generalizes_to_unseen_word_forms():
    train = {
        "docs": ["write docs readme explain", "document the api usage guide", "explain usage tutorial readme"],
        "bug": ["fix crash bug traceback error", "debug failing exception stack trace", "fix error bug crash"],
        "refactor": ["refactor rename extract module class", "restructure extract method rename", "refactor module cleanup class"],
        "algo": ["implement scheduler optimizer algorithm complexity", "design algorithm optimize complexity heap", "implement optimizer scheduling algorithm"],
    }
    test = {
        "docs": ["writing documentation explaining usage", "documented readmes", "explains the guides"],
        "bug": ["fixing crashes and errors", "debugging tracebacks", "fixed bugs crashing"],
        "refactor": ["refactoring modules renaming classes", "extracting methods", "refactored classes"],
        "algo": ["implementing schedulers optimizing algorithms", "optimization complexity", "algorithmic scheduling"],
    }
    proto = {k: np.mean([enc(t) for t in v], axis=0) for k, v in train.items()}
    hits = sum(max(proto, key=lambda c: enc(t) @ proto[c]) == k for k, ts in test.items() for t in ts)
    assert hits >= 11


def test_brain_persists(tmp_path):
    b = Brain(["a"], path=str(tmp_path / "b.npz"))
    for _ in range(16):
        b.update(enc("x y"), "a", 1.0)
    assert Brain(["a"], path=str(tmp_path / "b.npz")).i == 16


def test_gate():
    bus = Bus()
    g = Gate(bus)
    assert g.verdict("git push origin main") == "deny" and g.verdict("ls src") == "ask"

    async def teach(ok, n):
        for _ in range(n):
            await bus.emit(ToolAnswer("ls src", ok))

    asyncio.run(teach(True, 40))
    assert g.verdict("ls src") == "allow" and g.verdict("git push") == "deny"
    asyncio.run(teach(False, 40))
    assert g.verdict("ls src") == "ask"


def test_rules_merge_and_persist(tmp_path):
    bus = Bus()
    r = Rules(bus, tmp_path / "r.json")

    async def go():
        await bus.emit(Correction("Never add comments to code"))
        assert r.active == []
        await bus.emit(Correction("never add comments to code please"))
        await bus.emit(Correction("use polars not pandas"))

    asyncio.run(go())
    assert r.active == ["never add comments to code"] and Rules(Bus(), tmp_path / "r.json").active == r.active


def test_opposite_rules_with_the_same_words_never_merge(tmp_path):
    bus = Bus()
    r = Rules(bus, tmp_path / "r.json", k=1)

    async def go():
        await bus.emit(Correction("use polars not pandas"))
        await bus.emit(Correction("use pandas not polars"))

    asyncio.run(go())
    assert sorted(r.active) == ["use pandas not polars", "use polars not pandas"]


def test_agent_prompt_carries_persona_and_rules(tmp_path):
    class Echo(ClaudeCode):
        cmd = ("echo",)

    bus = Bus()
    r = Rules(bus, tmp_path / "r.json", k=1)
    asyncio.run(bus.emit(Correction("keep it tiny")))
    out = asyncio.run(Echo(PE, r).ask("do x"))
    assert "# T" in out and "rule: keep it tiny" in out and "do x" in out


def research(*rounds, **kw):
    bus, found = Bus(), []

    class Sink(Node):
        @on(Finding)
        async def f(s, f):
            found.append(f)

    Sink(bus)
    Researcher(bus, Fake(*rounds), **kw)
    asyncio.run(bus.emit(ResearchRequest("q")))
    return found[0]


def rnd(a, b):
    return json.dumps({"candidates": {"x": a, "y": b}, "gaps": []})


def test_researcher_converges_then_stops():
    f = research(rnd(.5, .5), rnd(.7, .3), rnd(.9, .1), rnd(.9, .1), rnd(.9, .1), rnd(.1, .9))
    assert f.reason == "converged" and f.rounds == 5


def test_researcher_budget_cap():
    f = research(rnd(.5, .5), rnd(.9, .1), rnd(.1, .9), rounds=4)
    assert f.reason == "budget" and f.rounds == 4


def test_pipeline_accept_and_learn(tmp_path):
    bus = Bus()
    brain, seen, panel = family(bus, [.9, .8], tmp_path)
    asyncio.run(bus.emit(Task("add feature", cwd=repo(tmp_path))))
    assert seen[0].accept and not seen[0].escalate and brain.i == 1


def test_red_tests_skip_the_paid_panel_and_still_teach(tmp_path):
    bus = Bus()
    brain, seen, panel = family(bus, [.9, .9], tmp_path, fix_rounds=0)
    asyncio.run(bus.emit(Task("add feature", cwd=repo(tmp_path, passing=False), id="t1")))
    assert not seen[0].accept and not seen[0].escalate and brain.i == 1
    assert all(p.agent.n == 0 for p in panel) and (tmp_path / "env/work/t1").exists()


def test_green_tests_but_panel_split_escalates_without_learning(tmp_path):
    bus = Bus()
    brain, seen, panel = family(bus, [.1, .1], tmp_path)
    asyncio.run(bus.emit(Task("add feature", cwd=repo(tmp_path))))
    assert seen[0].escalate and brain.i == 0


def test_malformed_ballot_is_abstention(tmp_path):
    bus = Bus()
    brain, seen, panel = family(bus, ["not json"], tmp_path)
    asyncio.run(bus.emit(Task("add feature", cwd=repo(tmp_path))))
    assert len(seen) == 1 and seen[0].accept


def test_cascade_escalates_to_pricier_member_and_succeeds(tmp_path):
    a, b = Fake("weak"), Fixer("strong")
    a.price, b.price = 1.0, 2.0
    bus = Bus()
    brain, seen, panel = family(bus, [.9], tmp_path, {"a": a, "b": b}, fix_rounds=0)
    asyncio.run(bus.emit(Task("hard job", cwd=repo(tmp_path, passing=False), id="t1", pin="a")))
    assert [(d.member, d.accept) for d in seen] == [("a", False), ("b", True)]
    assert (tmp_path / "env/work/t1").exists() and not (tmp_path / "env/work/t1+").exists()


class FixOnFeedback(Fake):
    async def ask(s, text, cwd=".", mcp=None):
        if "New information from the team" in text and "tests:" in text:
            Path(cwd, "test_x.py").write_text("def test_x():\n    assert True\n")
        return await super().ask(text, cwd, mcp)


def test_failing_tests_are_fed_back_to_the_same_builder_until_they_pass(tmp_path):
    a = FixOnFeedback("done")
    bus = Bus()
    brain, seen, panel = family(bus, [.9], tmp_path, {"a": a})
    asyncio.run(bus.emit(Task("fix it", cwd=repo(tmp_path, passing=False), id="t1", pin="a")))
    assert [(d.member, d.accept) for d in seen] == [("a", False), ("a", True)]
    assert "- tests:" in a.seen[-1] and "did not pass review" in a.seen[-1]


def test_review_feedback_gives_up_after_the_rounds_run_out_and_keeps_the_work(tmp_path):
    a = Fake("nope")
    bus = Bus()
    brain, seen, panel = family(bus, [.9], tmp_path, {"a": a}, fix_rounds=2)
    asyncio.run(bus.emit(Task("hopeless", cwd=repo(tmp_path, passing=False), id="t1", pin="a")))
    assert len(seen) == 3 and not any(d.accept for d in seen) and (tmp_path / "env/work/t1").exists()


def test_cascade_stops_at_top_tier(tmp_path):
    a, b = Fake("x"), Fake("y")
    a.price, b.price = 1.0, 2.0
    bus = Bus()
    brain, seen, panel = family(bus, [.9], tmp_path, {"a": a, "b": b}, fix_rounds=0)
    asyncio.run(bus.emit(Task("hopeless", cwd=repo(tmp_path, passing=False), pin="a")))
    assert [d.member for d in seen] == ["a", "b"]


def test_many_tasks_in_parallel(tmp_path):
    bus = Bus()
    brain, seen, panel = family(bus, [.9, .8], tmp_path)
    cwd = repo(tmp_path)

    async def go():
        await asyncio.gather(*(bus.emit(Task(f"job {i}", cwd=cwd)) for i in range(12)))

    asyncio.run(go())
    assert len(seen) == 12 and brain.i == 12


def test_missing_folder_fails_loudly_not_with_a_confusing_path_error(tmp_path):
    bus = Bus()
    family(bus, [.9], tmp_path)
    with pytest.raises(RuntimeError):
        asyncio.run(bus.emit(Task("x", cwd=str(tmp_path / "missing"))))


def test_ticker_survives_a_failing_subscriber():
    bus, n = Bus(), []

    class Bad(Node):
        @on(Tick)
        async def t(s, _):
            n.append(1)
            raise RuntimeError("boom")

    Bad(bus)
    try:
        asyncio.run(asyncio.wait_for(Ticker(bus).run(0.01), 0.1))
    except TimeoutError:
        pass
    assert len(n) >= 3
