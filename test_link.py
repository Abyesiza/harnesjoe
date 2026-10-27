import asyncio
import json

from core import Approve, Briefed, Bus, Node, Outcome, Proposal, Task, Tick, on
from link import SEEDS, Linker, config, ready


def sv(name, desc, pkg="@x/y", ver="1.0.0", reg="npm", env=(), transport="stdio"):
    return {"name": name, "description": desc, "version": ver, "repository": {"url": f"https://github.com/{name.split('/')[-1]}/{name.split('/')[-1]}"}, "packages": [{"registryType": reg, "identifier": pkg, "version": ver, "transport": {"type": transport}, "environmentVariables": [{"name": e} for e in env]}]}


SQLITE = sv("io.github.a/sqlite-mcp", "query sqlite databases", "@a/sqlite-mcp", "2.0.1")
COOK = sv("io.github.b/sql-cook", "recipes for cooking dinner", "@b/cook")


def linker(tmp_path, catalog=(), **kw):
    bus, props = Bus(), []

    class Sink(Node):
        @on(Proposal)
        async def p(s, p):
            props.append(p)

    Sink(bus)
    lk = Linker(bus, tmp_path / "env", find=lambda q: [x for x in catalog if q in x["name"]], **{"stars": lambda url: 42, **kw})
    lk.queue = []
    return bus, lk, props


def test_registry_entries_become_pinned_configs():
    assert config(SQLITE) == {"type": "stdio", "command": "npx", "args": ["-y", "@a/sqlite-mcp@2.0.1"]}
    assert config(sv("n", "d", "pkg", "3.1", "pypi"))["args"] == ["pkg@3.1"] and config(sv("n", "d", "img:1", "1", "oci"))["command"] == "docker"
    assert config(sv("n", "d", env=("TOKEN",)))["env"] == {"TOKEN": "${TOKEN}"}


def test_remote_servers_and_unsupported_ones():
    ok = {"name": "r", "remotes": [{"type": "streamable-http", "url": "https://x.dev/mcp"}]}
    auth = {"name": "r", "remotes": [{"type": "streamable-http", "url": "https://x.dev/mcp", "headers": [{"name": "Authorization", "isRequired": True}]}]}
    assert config(ok) == {"type": "http", "url": "https://x.dev/mcp"} and config(auth) is None
    assert config(sv("n", "d", reg="nuget")) is None


def test_servers_missing_credentials_are_not_ready(monkeypatch):
    cfg = config(sv("n", "d", env=("FAM_TEST_TOKEN",)))
    monkeypatch.delenv("FAM_TEST_TOKEN", raising=False)
    assert not ready(cfg)
    monkeypatch.setenv("FAM_TEST_TOKEN", "x")
    assert ready(cfg)


def test_first_run_queues_the_starter_searches(tmp_path):
    assert Linker(Bus(), tmp_path / "e").queue == list(SEEDS)


def test_idle_tick_proposes_but_never_trusts_then_approval_does(tmp_path):
    bus, lk, props = linker(tmp_path, [SQLITE])
    lk.queue = ["sqlite"]
    asyncio.run(bus.emit(Tick()))
    assert props[0].names == ["io.github.a/sqlite-mcp"] and lk.trusted == {} and lk.queue == []
    asyncio.run(bus.emit(Approve("io.github.a/sqlite-mcp")))
    assert "io.github.a/sqlite-mcp" in lk.trusted and not lk.props
    assert Linker(Bus(), tmp_path / "env").trusted.keys() == lk.trusted.keys()


def test_search_is_name_only_so_it_queries_each_word_and_ranks_by_meaning(tmp_path):
    bus, lk, props = linker(tmp_path, [COOK, SQLITE])
    hits = asyncio.run(lk.search("query a sqlite database"))
    assert [h["name"] for h in hits][0] == "io.github.a/sqlite-mcp"


def test_registry_outage_means_no_proposals_not_a_crash(tmp_path):
    def down(q):
        raise OSError("offline")

    bus, lk, props = linker(tmp_path)
    lk.find = down
    assert asyncio.run(lk.search("sqlite")) == [] and asyncio.run(lk.fulfil("sqlite database", "t1")) is None


def test_fulfil_proposes_when_nothing_is_trusted(tmp_path):
    bus, lk, props = linker(tmp_path, [SQLITE])
    assert asyncio.run(lk.fulfil("sqlite database", "t1")) is None and props and not lk.trusted


def test_auto_trust_attaches_immediately_with_a_strict_pinned_config(tmp_path):
    bus, lk, props = linker(tmp_path, [SQLITE], auto=True)
    path = asyncio.run(lk.fulfil("sqlite database", "t1"))
    cfg = json.loads(open(path).read())["mcpServers"]
    assert cfg == {"sqlite-mcp": {"type": "stdio", "command": "npx", "args": ["-y", "@a/sqlite-mcp@2.0.1"]}}


def test_trusted_server_without_credentials_is_skipped(tmp_path):
    bus, lk, props = linker(tmp_path, [])
    lk.trusted = {"io.github.a/sqlite-mcp": {"cfg": config(sv("n", "d", env=("FAM_NOPE_TOKEN",))), "desc": "query sqlite databases", "a": 1.0, "b": 1.0}}
    assert lk.match("sqlite database") == []


def test_empty_config_path_is_stable_and_empty(tmp_path):
    bus, lk, props = linker(tmp_path)
    a, b = lk.write(()), lk.write(())
    assert a == b and json.loads(open(a).read()) == {"mcpServers": {}}


def test_bad_outcomes_retire_a_server_for_good(tmp_path):
    bus, lk, props = linker(tmp_path, [SQLITE])
    name = "io.github.a/sqlite-mcp"
    lk.trusted = {name: {"cfg": config(SQLITE), "desc": "query sqlite databases", "a": 1.0, "b": 1.0}}

    async def go():
        for i in range(8):
            lk.used[f"t{i}"] = [name]
            await bus.emit(Outcome(Task("x", id=f"t{i}"), "a", 0.0))

    asyncio.run(go())
    assert not lk.trusted and name in lk.dead and asyncio.run(lk.search("sqlite")) == []


def test_good_outcomes_raise_a_servers_rank(tmp_path):
    bus, lk, props = linker(tmp_path)
    name = "io.github.a/sqlite-mcp"
    lk.trusted = {name: {"cfg": config(SQLITE), "desc": "query sqlite databases", "a": 1.0, "b": 1.0}}
    lk.used["t1"] = [name]
    asyncio.run(bus.emit(Outcome(Task("x", id="t1"), "a", 1.0)))
    assert lk.trusted[name]["a"] == 2.0 and lk.trusted[name]["b"] == 1.0


def test_expert_briefs_seed_discovery_off_the_critical_path(tmp_path):
    bus, lk, props = linker(tmp_path, [SQLITE])
    brief = {"rare": ["sqlite"]}
    asyncio.run(bus.emit(Briefed(Task("t"), brief, True)))
    assert lk.queue == []
    asyncio.run(bus.emit(Briefed(Task("t"), brief, False)))
    assert lk.queue == ["sqlite"] and not props
    asyncio.run(bus.emit(Tick()))
    assert props and lk.queue == []


def test_proposals_are_vetted_with_publisher_repo_stars_and_reason(tmp_path):
    # Arrange
    bus, lk, props = linker(tmp_path, [SQLITE])
    # Act
    asyncio.run(lk.fulfil("sqlite database", "t1"))
    # Assert
    p = lk.props["io.github.a/sqlite-mcp"]
    assert p["publisher"] == "io.github.a" and p["repo"].startswith("https://github.com/") and p["stars"] == 42
    assert "relevance" in p["why"] and not lk.trusted


def test_proposals_without_a_source_repository_are_skipped(tmp_path):
    # Arrange
    bare = {**SQLITE, "repository": {}}
    bus, lk, props = linker(tmp_path, [bare])
    # Act
    asyncio.run(lk.fulfil("sqlite database", "t1"))
    # Assert
    assert not lk.props and not props
