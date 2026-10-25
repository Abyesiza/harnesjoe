import asyncio
import os

import pytest

import sim
from agents import Agent, ClaudeCode, Ledger, OverBudget, Persona, Rules
from core import Bus, Focus, Lesson, Node, Output, ResearchRequest, Task, on
from family import Advisor, Suite
from mind import Brain, enc
from scout import Scout
from test_fam import Fake


def test_ledger_prices_output_at_five_times_input():
    led = Ledger(1.0)
    led.charge(2.0, 1_000_000, 0)
    led.charge(2.0, 0, 1_000_000)
    assert abs(led.spent - 12.0) < 1e-9 and led.over


def test_agent_refuses_past_budget(tmp_path):
    class Echo(ClaudeCode):
        cmd = ("echo",)

    old, Agent.ledger = Agent.ledger, Ledger(0.0)
    try:
        with pytest.raises(OverBudget):
            asyncio.run(Echo(Persona("T", "m"), Rules(Bus(), tmp_path / "r.json")).ask("x"))
    finally:
        Agent.ledger = old


def test_model_flag_and_price():
    a = ClaudeCode(Persona("T", "m"), None, "claude-haiku-5-5")
    assert a.argv("p") == ["claude", "-p", "--model", "claude-haiku-5-5", "p"] and a.price == 0.10


def test_wrap_prefix_is_applied(tmp_path):
    class Echo(ClaudeCode):
        cmd, wrap = ("hello",), ("echo",)

    out = asyncio.run(Echo(Persona("T", "m"), Rules(Bus(), tmp_path / "r.json")).ask("x"))
    assert out.startswith("hello")


def test_brain_state_on_disk_scales_with_use_not_capacity(tmp_path):
    b = Brain(["a"], path=str(tmp_path / "b.npz"))
    for _ in range(16):
        b.update(enc("x y"), "a", 1.0)
    assert os.path.getsize(tmp_path / "b.npz") < 100_000
    assert abs(Brain(["a"], path=str(tmp_path / "b.npz")).mean(enc("x y"))[0] - b.mean(enc("x y"))[0]) < 1e-6


def test_advisors_only_speak_on_green_and_only_as_candidates(tmp_path):
    bus, ideas = Bus(), []
    rules = Rules(bus, tmp_path / "r.json")
    Advisor(bus, "maverick", Fake('{"idea": "Use a hypervector memo"}'))
    Advisor(bus, "minimalist", Fake("garbage"))

    class Sink(Node):
        @on(Lesson)
        async def l(s, l):
            ideas.append(l.text)

    Sink(bus)

    class Fixed(Suite):
        async def rc(s, o):
            return s.code

    green, red = Fixed(bus), None
    green.code = 0
    asyncio.run(bus.emit(Output(Task("t", cwd=str(tmp_path)), "a", "x")))
    assert ideas == ["Use a hypervector memo"] and rules.active == [] and "use a hypervector memo" in rules.cand
    ideas.clear()
    green.code = 1
    asyncio.run(bus.emit(Output(Task("t2", cwd=str(tmp_path)), "a", "x")))
    assert ideas == []


def test_focus_retargets_scout_and_starts_research(tmp_path):
    bus, asked = Bus(), []

    class Sink(Node):
        @on(ResearchRequest)
        async def r(s, r):
            asked.append(r.question)

    Sink(bus)
    sc = Scout(bus, Fake("{}"), ["q"], tmp_path / "fam", find=lambda q: [])
    asyncio.run(bus.emit(Focus("well drilling engineering")))
    assert sc.field == "well drilling engineering" and all("well drilling engineering" in o for o in sc.brain.opt)
    assert "well drilling engineering" in asked[0]


def test_simulation_cascade_matches_opus_quality_for_less_money_and_fewer_retries():
    opus = asyncio.run(sim.episode("opus", 900, tries=1))
    cheap = asyncio.run(sim.episode("haiku", 900))
    learned = asyncio.run(sim.episode(None, 900))
    assert learned["cost_task"] < 0.8 * opus["cost_task"]
    assert learned["final"] > opus["final"] - 0.05
    assert learned["retries"] < 0.7 * cheap["retries"]
    assert learned["hit_rate"] > 0.4 and learned["wrong_hits"] < 0.05
