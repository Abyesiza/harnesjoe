import asyncio
import json
from pathlib import Path

import app
from agents import ClaudeCode, Copilot, Persona
from core import Blocked, Bus, Decision, Lesson, Node, Output, Said, Task, Tossed
from family import Advisor, Green, Lead, Panel, flow_of
from link import Linker, config
from mind import Brain
from test_fam import Fake, family, repo
from test_link import SQLITE


def watch(bus, *events):
    seen = []

    class Sink(Node):
        pass

    for ev in events:
        async def h(s, e, ev=ev):
            seen.append(e)

        h.ev = ev
        setattr(Sink, f"h_{ev.__name__}", h)
    Sink(bus)
    return seen


def test_directives_pick_the_flow():
    assert flow_of("add login, build after test") == "tdd" and flow_of("tests first please") == "tdd"
    assert flow_of("TDD this") == "tdd" and flow_of("quick fix") == "quick" and flow_of("add login") == "plan"


def test_persona_renders_its_voice_and_keeps_machine_output_exact():
    r = Persona("Nova", "m", (), "bold and playful").render()
    assert "# Nova" in r and "bold and playful" in r and "keep code, JSON and commands exact" in r
    assert "Voice" not in Persona("X", "m").render()


def test_mcp_flags_come_before_the_prompt_and_only_when_asked():
    a = ClaudeCode(Persona("T", "m"), None, "claude-haiku-5-5")
    assert a.argv("p", "/tmp/c.json") == ["claude", "-p", "--model", "claude-haiku-5-5", "--strict-mcp-config", "--mcp-config", "/tmp/c.json", "p"]
    assert "--mcp-config" not in a.argv("p")


def test_build_uses_both_installed_cli_providers(tmp_path, monkeypatch):
    monkeypatch.setattr(app.shutil, "which", lambda command: f"/usr/bin/{command}")

    built = app.build(tmp_path)

    assert isinstance(built.skipper, ClaudeCode)
    assert isinstance(built.cast["spark"], ClaudeCode)
    assert isinstance(built.cast["pip"], Copilot)
    assert isinstance(built.cast["bastion"], Copilot)
    assert Copilot(Persona("Pip", "m"), None).argv("hello") == ["copilot", "-p", "hello"]


def test_tdd_flow_tosses_blue_then_tess_before_the_build(tmp_path):
    bus = Bus()
    impl, blue, tess = Fake("built"), Fake("the design"), Fake("the tests")
    toss = watch(bus, Tossed)
    brain, seen, panel = family(bus, [.9], tmp_path, {"a": impl}, crew={"blue": blue, "tess": tess})
    asyncio.run(bus.emit(Task("add login, build after test", cwd=repo(tmp_path))))
    assert [t.to for t in toss] == ["blue", "tess"]
    assert "[blue] the design" in impl.seen[0] and "[tess] the tests" in impl.seen[0] and "the design" in tess.seen[0]


def test_quick_flow_skips_the_crew(tmp_path):
    bus = Bus()
    impl, blue, tess = Fake("built"), Fake("d"), Fake("t")
    brain, seen, panel = family(bus, [.9], tmp_path, {"a": impl}, crew={"blue": blue, "tess": tess})
    asyncio.run(bus.emit(Task("quick rename", cwd=repo(tmp_path))))
    assert blue.n == 0 and tess.n == 0 and impl.n == 1


def test_skippers_router_prioritizes_available_members(tmp_path):
    members = {"a": Fake("done"), "b": Fake("done")}
    lead = Lead(Bus(), members, Brain(list(members)), tmp_path)
    lead.active["a"].add("busy")

    assert lead.select("add a feature") == "b"


def test_accepted_change_is_applied_only_when_repository_is_clean(tmp_path):
    work = tmp_path / "source"
    work.mkdir()
    repo(work)

    class AddFile(Fake):
        async def ask(s, text, cwd=".", mcp=None):
            Path(cwd, "feature.txt").write_text("accepted\n")
            return "Added the feature."

    bus = Bus()
    impl = AddFile("done")
    family(bus, [.9], work, {"a": impl}, root=tmp_path / "runtime", apply_accepted=True)
    asyncio.run(bus.emit(Task("add a feature", cwd=str(work))))

    assert (work / "feature.txt").read_text() == "accepted\n"
    assert not list(work.glob("fam-*"))


def test_accepted_change_stays_isolated_when_repository_is_dirty(tmp_path):
    work = tmp_path / "source"
    work.mkdir()
    repo(work)
    (work / "user.txt").write_text("keep my edit\n")

    class AddFile(Fake):
        async def ask(s, text, cwd=".", mcp=None):
            Path(cwd, "feature.txt").write_text("accepted\n")
            return "Added the feature."

    bus = Bus()
    impl = AddFile("done")
    family(bus, [.9], work, {"a": impl}, root=tmp_path / "runtime", apply_accepted=True)
    asyncio.run(bus.emit(Task("add a feature", cwd=str(work))))

    assert (work / "user.txt").read_text() == "keep my edit\n"
    assert not (work / "feature.txt").exists()


def test_builders_always_get_a_strict_tool_surface_and_the_need_protocol(tmp_path):
    bus = Bus()
    impl = Fake("built")
    lk = Linker(bus, tmp_path / "lk", find=lambda q: [])
    brain, seen, panel = family(bus, [.9], tmp_path, {"a": impl}, linker=lk)
    asyncio.run(bus.emit(Task("quick job", cwd=repo(tmp_path))))
    assert json.loads(open(impl.mcp[0]).read()) == {"mcpServers": {}} and "NEED:" in impl.seen[0]


def test_a_need_is_met_by_a_trusted_server_and_the_task_continues(tmp_path):
    bus = Bus()
    impl = Fake("NEED: sqlite database", "built")
    lk = Linker(bus, tmp_path / "lk", find=lambda q: [])
    lk.queue = []
    lk.trusted = {"io.github.a/sqlite-mcp": {"cfg": config(SQLITE), "desc": "query sqlite databases", "a": 1.0, "b": 1.0}}
    brain, seen, panel = family(bus, [.9], tmp_path, {"a": impl}, linker=lk)
    asyncio.run(bus.emit(Task("quick job", cwd=repo(tmp_path))))
    assert "sqlite-mcp" in open(impl.mcp[1]).read() and impl.mcp[0] != impl.mcp[1]
    assert seen[0].accept and lk.trusted["io.github.a/sqlite-mcp"]["a"] > 1.0


def test_an_unmet_need_blocks_cleanly_with_a_proposal_and_no_leftovers(tmp_path):
    bus = Bus()
    impl = Fake("NEED: sqlite database")
    lk = Linker(bus, tmp_path / "lk", find=lambda q: [SQLITE] if q == "sqlite" else [])
    lk.queue = []
    blocked = watch(bus, Blocked, Output, Decision)
    brain, seen, panel = family(bus, [.9], tmp_path, {"a": impl}, linker=lk)
    asyncio.run(bus.emit(Task("quick job", cwd=repo(tmp_path), id="t1")))
    assert [type(b) for b in blocked] == [Blocked] and blocked[0].need == "sqlite database"
    assert "io.github.a/sqlite-mcp" in lk.props and not lk.trusted and not (tmp_path / "env/work/t1").exists()


def test_reviewers_and_advisors_speak_in_voice_and_ideas_keep_their_author(tmp_path):
    bus = Bus()
    said = watch(bus, Said, Lesson)
    Panel(bus, "Vera", Fake('{"accept": 0.9, "say": "Proof holds."}'))
    Advisor(bus, "Nova", Fake('{"idea": "Flip the cache key", "say": "What if we flipped it?"}'))
    asyncio.run(bus.emit(Green(Task("t", cwd=str(tmp_path)), "a")))
    assert {(type(e).__name__, getattr(e, "name", None) or e.by) for e in said} >= {("Said", "Vera"), ("Said", "Nova"), ("Lesson", "Nova")}


def test_the_family_is_grand_named_and_has_a_voice_each(tmp_path):
    assert len({p.name for p in app.P.values()}) == 15 and all(p.voice for p in app.P.values())
    a = app.build(tmp_path)
    assert set(a.cast) == set(app.P) and a.lead.crew is a.cast
    assert app.line("toss", "x", to="Tess", what="write tests").count("Tess") == 1
