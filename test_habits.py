"""The family learns how the user works (Arrange / Act / Assert)."""

import asyncio
import subprocess

import pytest

from agents import ClaudeCode, Persona
from core import Bus, Correction, Said, Node, on
from habits import Profile


class Sage:
    def __init__(self, reply):
        self.reply = reply

    async def ask(self, prompt, cwd=".", mcp=None):
        return self.reply


def make(tmp_path, reply='{"habits": []}', **kw):
    bus = Bus()
    return bus, Profile(bus, Sage(reply), tmp_path, **kw)


def test_stated_preferences_count_at_once_and_persist(tmp_path):
    bus, p = make(tmp_path)
    asyncio.run(bus.emit(Correction("Keep functions under 20 lines")))
    assert p.text().count("Keep functions under 20 lines") == 1
    assert Profile(Bus(), Sage(""), tmp_path).habits == p.habits


def test_noticed_habits_need_a_second_sighting(tmp_path):
    _, p = make(tmp_path)
    p.add("Prefer early returns", "your commits")
    assert p.text() == ""
    p.add("prefer early returns", "your commits")
    assert "Prefer early returns" in p.text()


def test_forget_and_report(tmp_path):
    _, p = make(tmp_path)
    p.add("Name tests after behavior", "you said")
    assert "1. Name tests after behavior" in p.report()
    assert p.forget(1) == "Name tests after behavior" and p.habits == []
    with pytest.raises(ValueError):
        p.forget(1)


def test_agents_prompt_carries_the_profile_without_repeating_rules(tmp_path):
    _, p = make(tmp_path)
    p.add("Keep it small", "you said")
    p.add("No comments", "you said")
    agent = ClaudeCode(Persona("Ember", "build"), None)
    agent.profile = p
    prompt = agent.prompt("do x")
    assert "- Keep it small" in prompt and "- No comments" in prompt
    assert "- No comments" not in ClaudeCode.prompt(_with(agent, p, {"No comments"}), "do x")


def _with(agent, p, skip):
    class Rules:
        active = list(skip)

    agent.rules = Rules()
    return agent


def test_study_reads_the_users_commits_and_requests(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    run = lambda *a: subprocess.run(a, cwd=repo, check=True, capture_output=True)
    run("git", "init", "-q")
    (repo / "a.py").write_text("x = 1\n")
    run("git", "add", ".")
    run("git", "-c", "user.email=me@x", "-c", "user.name=me", "commit", "-qm", "init")
    run("git", "config", "user.email", "me@x")
    said = []

    class Spy(Node):
        @on(Said)
        async def s(self, e):
            said.append(e.text)

    msgs = [{"role": "user", "text": f"keep reports short {i}"} for i in range(9)]
    bus, p = make(tmp_path / "home", '{"habits": ["Keep changes small"]}', folders=lambda: [str(repo)], messages=lambda n: msgs)
    Spy(bus)
    new = asyncio.run(p.study())
    assert new == ["Keep changes small"] and said and "Keep changes small" in said[0]
    assert {h["source"] for h in p.habits} == {"your commits"}
    assert asyncio.run(p.study()) == []


def test_build_exposes_the_profile_for_the_server(tmp_path):
    import app

    built = app.build(tmp_path)
    assert built.profile is built.cast["ember"].profile and built.skipper.profile is built.profile
