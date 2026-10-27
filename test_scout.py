import asyncio
import itertools
import subprocess
import urllib.error

from agents import Agent, Rules
from core import Bus, Correction, Node, Scouted, Tick, Ticker, on
from scout import Scout
from test_fam import Fake


def local_repo(tmp_path):
    d = tmp_path / "src"
    (d / "skills" / "s1").mkdir(parents=True)
    (d / "skills" / "s1" / "SKILL.md").write_text("x")
    run = lambda *a: subprocess.run(a, cwd=d, check=True, capture_output=True)
    run("git", "init", "-q")
    run("git", "add", ".")
    run("git", "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", "i")
    return f"file://{d}"


def hit(url, name="o/n", **kw):
    return {"full_name": name, "clone_url": url, "size": 10, "fork": False, "archived": False, "pushed_at": "t1", **kw}


def make_scout(tmp_path, find, reply, queries=("q",)):
    bus, seen = Bus(), []

    class Sink(Node):
        @on(Scouted)
        async def s(s, e):
            seen.append(e)

    Sink(bus)
    rules = Rules(bus, tmp_path / "rules.json")
    return bus, rules, Scout(bus, Fake(reply), list(queries), tmp_path / "fam", find=find), seen


GOOD = '{"score": 0.9, "lessons": ["Never duplicate a class"]}'


def test_pulls_skills_and_quarantines_lessons(tmp_path):
    bus, rules, sc, seen = make_scout(tmp_path, lambda q: [hit(local_repo(tmp_path))], GOOD)
    asyncio.run(bus.emit(Tick()))
    assert (tmp_path / "fam/skills/o__n/skills/s1/SKILL.md").exists() and sc.cat["o/n"]["score"] == 0.9
    assert rules.active == [] and list(rules.cand) == ["never duplicate a class"]
    asyncio.run(bus.emit(Correction("never duplicate a class")))
    assert rules.active == ["never duplicate a class"] and not rules.cand


def test_filters_and_skips_unchanged(tmp_path):
    url = local_repo(tmp_path)
    hits = [hit(url, "a/fork", fork=True), hit(url, "a/old", archived=True), hit(url, "a/huge", size=10**9), hit(url)]
    bus, rules, sc, seen = make_scout(tmp_path, lambda q: hits, GOOD)
    asyncio.run(bus.emit(Tick()))
    asyncio.run(bus.emit(Tick()))
    assert list(sc.cat) == ["o/n"] and len(seen) == 1


def test_low_score_pulls_nothing(tmp_path):
    bus, rules, sc, seen = make_scout(tmp_path, lambda q: [hit(local_repo(tmp_path))], '{"score": 0.3, "lessons": ["x"]}')
    asyncio.run(bus.emit(Tick()))
    assert not (tmp_path / "fam/skills").exists() and not rules.cand and len(seen) == 1


def test_malformed_review_is_marked_and_not_learned(tmp_path):
    bus, rules, sc, seen = make_scout(tmp_path, lambda q: [hit(local_repo(tmp_path))], "nope")
    asyncio.run(bus.emit(Tick()))
    assert sc.cat["o/n"]["score"] is None and not seen and sc.brain.i == 0


def test_network_error_is_ignored(tmp_path):
    def down(q):
        raise urllib.error.URLError("offline")

    bus, rules, sc, seen = make_scout(tmp_path, down, GOOD)
    asyncio.run(bus.emit(Tick()))
    assert sc.brain.i == 0 and not seen


def test_learns_which_query_pays(tmp_path):
    url, n, asked = local_repo(tmp_path), itertools.count(), []

    def find(q):
        asked.append(q)
        return [hit(url, f"o/r{next(n)}")] if q == "good" else []

    bus, rules, sc, seen = make_scout(tmp_path, find, GOOD, ("good", "bad"))

    async def go():
        for _ in range(40):
            await bus.emit(Tick())

    asyncio.run(go())
    assert asked[-20:].count("good") >= 15


def test_defers_when_agents_busy(tmp_path):
    called = []
    bus, rules, sc, seen = make_scout(tmp_path, lambda q: called.append(q) or [], GOOD)

    async def go():
        for _ in range(Agent.limit):
            await Agent.sem.acquire()
        await bus.emit(Tick())
        for _ in range(Agent.limit):
            Agent.sem.release()

    asyncio.run(go())
    assert called == []


def test_ticker_runs_without_any_task():
    bus, ticks = Bus(), []

    class Sink(Node):
        @on(Tick)
        async def t(s, _):
            ticks.append(1)

    Sink(bus)
    try:
        asyncio.run(asyncio.wait_for(Ticker(bus).run(0.01), 0.1))
    except TimeoutError:
        pass
    assert len(ticks) >= 3
