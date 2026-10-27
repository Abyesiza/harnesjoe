"""Tests for implicit folder access, CLI fallback, web research parsing, auth and run tracking (AAA style)."""
import asyncio
import os
from pathlib import Path

import pytest

import agents
import server
import websearch
from agents import Agent, AgentError, ClaudeCode, Copilot
from skipper import Skipper, StateStore, clean_plan, folder_in, safe_folder


class Persona:
    def render(self, rules):
        return "persona"


class Recorder:
    def __init__(self):
        self.events = []

    def start(self, cli, cwd, prompt):
        self.events.append(("start", cli))
        return "r1"

    def fallback(self, run_id, failed, to, reason):
        self.events.append(("fallback", failed, to))

    def finish(self, run_id, status, output, cli=""):
        self.events.append(("finish", status, cli))


@pytest.fixture
def store(tmp_path):
    return StateStore(tmp_path / "fam" / "state.sqlite3")


# ---- folders -------------------------------------------------------------

def test_safe_folder_accepts_project_directory(tmp_path):
    # Arrange
    project = tmp_path / "app"
    project.mkdir()
    # Act
    result = safe_folder(str(project))
    # Assert
    assert result == project.resolve()


@pytest.mark.parametrize("raw", ["relative/dir", "/", "/etc", "/usr/lib", str(Path.home())])
def test_safe_folder_rejects_relative_system_and_home_root(raw):
    with pytest.raises(ValueError):
        safe_folder(raw)


def test_safe_folder_rejects_credential_dirs(tmp_path):
    # Arrange
    secret = tmp_path / ".ssh" / "keys"
    secret.mkdir(parents=True)
    # Act / Assert
    with pytest.raises(ValueError):
        safe_folder(str(secret))


def test_safe_folder_resolves_traversal_and_symlinks(tmp_path):
    # Arrange
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    # Act
    via_dots = safe_folder(str(tmp_path / "real" / ".." / "real"))
    via_link = safe_folder(str(tmp_path / "link"))
    # Assert
    assert via_dots == via_link == real.resolve()


def test_symlink_into_system_dir_is_rejected(tmp_path):
    # Arrange
    (tmp_path / "sneaky").symlink_to("/etc")
    # Act / Assert
    with pytest.raises(ValueError):
        safe_folder(str(tmp_path / "sneaky"))


def test_grant_folder_is_idempotent_and_authorizes_subtree(store, tmp_path):
    # Arrange
    project = tmp_path / "app"
    (project / "src").mkdir(parents=True)
    # Act
    first = store.grant_folder(str(project))
    second = store.grant_folder(str(project))
    # Assert
    assert (first, second) == (True, False)
    assert store.approved_folders().count(str(project)) == 1
    assert Skipper(store).authorized(project / "src")
    assert not Skipper(store).authorized(tmp_path)


def test_approving_duplicate_resolved_path_does_not_crash(store, tmp_path):
    # Arrange
    project = tmp_path / "app"
    project.mkdir()
    store.grant_folder(str(project))
    pending = store.request_folder(str(tmp_path / "app" / ".." / "app"))
    # Act
    store.approve_folder(pending["id"], str(project))
    # Assert
    assert store.approved_folders() == [str(project)]
    assert store.pending_folders() == []


def test_folder_in_finds_named_directory(tmp_path):
    # Arrange
    project = tmp_path / "app"
    project.mkdir()
    # Act
    found = folder_in(f"please review {project}, thanks")
    # Assert
    assert found == str(project)
    assert folder_in("no path here") is None


# ---- plan validation -----------------------------------------------------

def test_clean_plan_bounds_fields_and_rejects_unknown_intent():
    # Arrange
    plan = {"intent": "task", "task": "x" * 9000, "member": "PIP", "extra": "dropped"}
    # Act
    cleaned = clean_plan(plan)
    # Assert
    assert len(cleaned["task"]) == 8000 and cleaned["member"] == "pip" and "extra" not in cleaned
    with pytest.raises(ValueError):
        clean_plan({"intent": "rm -rf"})
    with pytest.raises(ValueError):
        clean_plan(["not", "a", "dict"])


def split(fake):
    """Adapt a (rc, out) fake to sh_split's (rc, stdout, stderr)."""
    async def run(*argv, cwd=None, input=None, timeout=None, env=None, merge=False):
        rc, out = await fake(*argv, cwd=cwd, input=input, timeout=timeout, env=env)
        return rc, out, ""
    return run


# ---- CLI fallback --------------------------------------------------------

def run_agent(monkeypatch, responses):
    calls = []

    async def fake_sh(*argv, cwd=None, input=None, timeout=None, env=None):
        calls.append(argv[0])
        result = responses[argv[0]]
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(agents, "sh_split", split(fake_sh))
    tracker = Recorder()
    monkeypatch.setattr(Agent, "tracker", tracker)
    agent = Copilot(Persona(), None, fallbacks=[ClaudeCode(Persona(), None)])
    return agent, calls, tracker


@pytest.mark.parametrize("failure", [(1, "boom"), (0, "   "), FileNotFoundError("copilot"), TimeoutError()])
def test_copilot_failure_falls_back_to_claude(monkeypatch, failure):
    # Arrange
    agent, calls, tracker = run_agent(monkeypatch, {"copilot": failure, "claude": (0, "fixed")})
    # Act
    out = asyncio.run(agent.ask("do it"))
    # Assert
    assert out == "fixed"
    assert calls == ["copilot", "claude"]
    assert tracker.events == [("start", "copilot"), ("fallback", "copilot", "claude"), ("finish", "done", "claude")]


def test_all_clis_failing_raises_and_records_failure(monkeypatch):
    # Arrange
    agent, _, tracker = run_agent(monkeypatch, {"copilot": (1, "a"), "claude": (2, "b")})
    # Act / Assert
    with pytest.raises(AgentError):
        asyncio.run(agent.ask("do it"))
    assert tracker.events[-1] == ("finish", "failed", "claude")


# ---- web research --------------------------------------------------------

DDG = '''
<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F3%2Flibrary%2Fasyncio.html&amp;rut=x">asyncio &amp; <b>docs</b></a>
<a class="result__snippet" href="#">Asynchronous <b>I/O</b> library.</a>
<a rel="nofollow" class="result__a" href="https://example.com/b">Second</a>
<a class="result__snippet" href="#">Another.</a>
'''


def test_parse_ddg_unwraps_redirects_and_strips_markup():
    # Act
    hits = websearch.parse_ddg(DDG)
    # Assert
    assert [h.url for h in hits] == ["https://docs.python.org/3/library/asyncio.html", "https://example.com/b"]
    assert hits[0].title == "asyncio & docs" and hits[0].snippet == "Asynchronous I/O library."


def test_search_falls_through_providers_and_fetches_pages(monkeypatch):
    # Arrange
    monkeypatch.setenv("BRAVE_API_KEY", "k")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    def broken(query, limit):
        raise OSError("down")

    monkeypatch.setattr(websearch, "brave", broken)
    monkeypatch.setattr(websearch, "_get", lambda url, data=None, headers=None: DDG if "duckduckgo" in url else "<html><script>x</script><p>Page body</p></html>")
    # Act
    results = websearch.search("asyncio", limit=2, fetch=1)
    # Assert
    assert [r["provider"] for r in results] == ["duckduckgo", "duckduckgo"]
    assert results[0]["text"] == "Page body" and results[1]["text"] == ""


# ---- auth + run tracking -------------------------------------------------

class Feed:
    def __init__(self):
        self.pushed = []

    def push(self, kind, data):
        self.pushed.append((kind, data))


@pytest.fixture
def api(monkeypatch, store):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(server.S, "store", store)
    monkeypatch.setattr(server.S, "feed", Feed())
    monkeypatch.setattr(server.S, "token", "secret-token")
    return TestClient(server.api, base_url="http://localhost")


def test_write_endpoints_require_token(api, tmp_path):
    # Arrange
    project = tmp_path / "app"
    project.mkdir()
    body = {"path": str(project)}
    # Act
    missing = api.post("/api/folders/request", json=body)
    wrong = api.post("/api/folders/request", json=body, headers={"X-Fam-Token": "nope"})
    ok = api.post("/api/folders/request", json=body, headers={"X-Fam-Token": "secret-token"})
    # Assert
    assert (missing.status_code, wrong.status_code, ok.status_code) == (401, 401, 200)
    assert ok.json() == {"path": str(project.resolve()), "approved": True}
    assert server.S.feed.pushed[0][0] == "FolderApproved"


def test_token_file_is_private(tmp_path):
    # Act
    token = server.write_token(tmp_path)
    # Assert
    assert (tmp_path / "token").read_text() == token and len(token) >= 32
    assert oct(os.stat(tmp_path / "token").st_mode & 0o777) == "0o600"


def test_run_tracker_records_fallback_and_completion(api):
    # Arrange
    tracker = server.RunTracker()
    # Act
    run_id = tracker.start("copilot", "/w", "fix   the\nbug")
    tracker.fallback(run_id, "copilot", "claude", "exit 1")
    tracker.finish(run_id, "done", "all good", "claude")
    rows = api.get("/api/runs").json()
    # Assert
    assert rows[0]["id"] == run_id and rows[0]["status"] == "done" and rows[0]["cli"] == "claude"
    assert rows[0]["prompt"] == "fix the bug"
    assert rows[0]["fallbacks"] == [{"failed": "copilot", "to": "claude", "reason": "exit 1"}]
    assert [k for k, _ in server.S.feed.pushed] == ["Run", "Fallback", "Run"]


def test_interrupted_runs_are_marked_on_restart(store):
    # Arrange
    store.run_start("claude", "/w", "x")
    # Act
    count = store.interrupt_runs()
    # Assert
    assert count == 1 and store.runs()[0]["status"] == "interrupted"


# ---- named folders reach every CLI ---------------------------------------

from core import ACCESS, Access, Bus, ResearchRequest
from family import Researcher
from skipper import folders_in


@pytest.fixture
def spaced(tmp_path):
    folder = tmp_path / "Metis Projects"
    (folder / "app").mkdir(parents=True)
    return folder


@pytest.mark.parametrize("template", [
    "Look through {p}, read-only. List what projects are there",
    'Survey "{p}" please',
    "Survey '{p}'",
    "Survey {p}",
    "Survey {p}/ now",
])
def test_folders_in_keeps_spaces(spaced, template):
    # Act
    found = folders_in(template.format(p=spaced))
    # Assert
    assert found == [str(spaced)]


def test_folders_in_finds_several_and_ignores_missing(spaced, tmp_path):
    # Arrange
    other = tmp_path / "other"
    other.mkdir()
    # Act
    found = folders_in(f"compare {spaced} with {other} and /no/such/dir")
    # Assert
    assert found == [str(spaced), str(other)]


def test_claude_read_only_access_args(spaced):
    # Arrange
    agent = ClaudeCode(Persona(), None)
    token = ACCESS.set(Access((str(spaced),), readonly=True))
    # Act
    try:
        argv = agent.argv("survey")
    finally:
        ACCESS.reset(token)
    # Assert
    assert argv[argv.index("--add-dir") + 1] == str(spaced)
    allowed = argv[argv.index("--allowedTools") + 1: argv.index("--disallowedTools")]
    assert {"Read", "Glob", "Grep", "Bash(ls:*)", "Bash(git status:*)"} <= set(allowed)
    assert not any(t.startswith(("Edit", "Write", "Bash(rm")) for t in allowed)
    assert argv[-2:] == ["--", "survey"]


def test_copilot_read_only_access_args(spaced):
    # Arrange
    agent = Copilot(Persona(), None)
    token = ACCESS.set(Access((str(spaced),), readonly=True))
    # Act
    try:
        argv = agent.argv("survey")
    finally:
        ACCESS.reset(token)
    # Assert
    assert argv[:3] == ["copilot", "-p", "survey"]
    assert argv[argv.index("--add-dir") + 1] == str(spaced)
    assert "shell(ls)" in argv and "shell(git status)" in argv
    assert argv[argv.index("--deny-tool") + 1] == "write"


def test_no_access_args_by_default():
    assert ClaudeCode(Persona(), None).argv("x") == ["claude", "-p", "--", "x"]


def test_research_job_runs_in_named_folder_with_read_only_access_on_fallback(monkeypatch, spaced):
    # Arrange
    calls = []

    async def fake_sh(*argv, cwd=None, input=None, timeout=None, env=None):
        calls.append((list(argv), cwd))
        if argv[0] == "copilot":
            return 1, "denied"
        return 0, '{"candidates": {"app": 1.0}, "gaps": [], "say": "found app", "confidence": 0.9}'

    monkeypatch.setattr(agents, "sh_split", split(fake_sh))
    monkeypatch.setattr(Agent, "tracker", None)
    searched = []
    quill = Copilot(Persona(), None, fallbacks=[ClaudeCode(Persona(), None)])
    researcher = Researcher(Bus(), quill, rounds=1, web=lambda q: searched.append(q) or [])
    # Act
    asyncio.run(researcher.dig(ResearchRequest("survey it read-only", [str(spaced)])))
    # Assert
    assert [c[0][0] for c in calls] == ["copilot", "claude"]
    for argv, cwd in calls:
        assert cwd == str(spaced)
        assert argv[argv.index("--add-dir") + 1] == str(spaced)
    assert "--allowedTools" in calls[1][0]
    assert searched == []  # local paths are never sent to a web search engine


# ---- research follow-ups vs new jobs, and early stopping -----------------

from core import Finding, Node, on
from skipper import is_research_followup


@pytest.mark.parametrize("text", [
    "Ask Quill to research this: list the projects in /home/joel-abyesiza/Metis Projects, read-only",
    "Research the best Python task queue, including status of maintenance",
    "Quill, look through the repo and tell me if tests are done",
    "research how far apart the releases are",
])
def test_new_research_requests_are_not_replayed(text):
    assert is_research_followup(text) is False


@pytest.mark.parametrize("text", ["What did Quill find?", "show me the last research result", "Is Quill done yet?", "quill's progress?", "research status"])
def test_explicit_followups_replay(text):
    assert is_research_followup(text) is True


class Scripted:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    async def ask_json(self, text, cwd="."):
        self.calls.append(cwd)
        return self.replies.pop(0)


def dig(agent, request, **kw):
    bus, found = Bus(), []

    class Sink(Node):
        @on(Finding)
        async def got(self, f):
            found.append(f)

    Sink(bus)
    asyncio.run(Researcher(bus, agent, web=lambda q: [], **kw).dig(request))
    return found[0]


def test_local_folder_job_is_capped_at_two_rounds(spaced):
    # Arrange
    agent = Scripted(*({"candidates": {"x": i / 10}, "gaps": ["more"]} for i in range(8)))
    # Act
    finding = dig(agent, ResearchRequest("list projects", [str(spaced)]))
    # Assert
    assert finding.rounds == 2 and len(agent.calls) == 2 and finding.reason == "budget"


def test_research_settles_early_when_model_is_done():
    # Arrange
    agent = Scripted({"candidates": {"a": 0.6}, "gaps": ["x"]}, {"candidates": {"a": 0.9}, "gaps": [], "done": True}, {"candidates": {"b": 1}})
    # Act
    finding = dig(agent, ResearchRequest("q"))
    # Assert
    assert finding.rounds == 2 and finding.reason == "settled"


@pytest.mark.parametrize("reply", [{"candidates": {"a": 1}, "gaps": ["open"], "done": True}, {"candidates": {}, "gaps": [], "done": True}, {"candidates": {"a": 1}, "gaps": [], "done": "yes"}])
def test_done_without_answer_or_with_gaps_does_not_settle(reply):
    # Arrange
    agent = Scripted(reply, reply, reply)
    # Act
    finding = dig(agent, ResearchRequest("q"), rounds=3, patience=99)
    # Assert
    assert finding.rounds == 3 and finding.reason == "budget"


def test_ask_quill_to_research_starts_a_new_job(tmp_path):
    # Arrange
    from test_skipper import app
    from skipper import Skipper
    from server import StateStore
    site, queued = app(tmp_path), []
    folder = tmp_path / "My Projects"
    folder.mkdir()
    state = StateStore(tmp_path / "state.sqlite3")
    # Act
    result = asyncio.run(Skipper(state, site.skipper).chat(
        f"Ask Quill to research this: list the projects in {folder}, read-only", str(tmp_path), site, lambda e, *a: queued.append(e)))
    # Assert
    assert isinstance(queued[0], ResearchRequest) and queued[0].folders == [str(folder)]
    assert result["research"] == queued[0].id


# ---- reports, provider tracking, restart ---------------------------------

import reports


def test_research_report_is_structured_and_names_provider():
    # Arrange
    finding = {"id": "j1", "question": "best queue", "candidates": {"celery": 0.8, "rq": 0.2}, "rounds": 2, "reason": "settled",
               "say": "Celery fits.", "sources": [{"title": "t", "url": "https://a.dev", "provider": "tavily"}]}
    runs = [{"cli": "copilot", "status": "error", "started": 0.0, "ended": 5.0, "fallbacks": [{"failed": "copilot", "to": "claude", "reason": "quota"}]},
            {"cli": "claude", "status": "done", "started": 5.0, "ended": 65.0, "fallbacks": []}]
    # Act
    text = reports.research(finding, runs)
    # Assert
    for part in ("Asked: best queue", "Result: Celery fits.", "via tavily", "copilot→claude (quota)", "took 1m 5s", "Problems: none", "settled early"):
        assert part in text


def test_research_report_flags_missing_answer():
    # Arrange / Act
    text = reports.research({"id": "j2", "question": "q", "candidates": {}, "rounds": 1, "reason": "budget"}, [])
    # Assert
    assert "Problems: no answer reached" in text and "no CLI calls recorded" in text


def test_error_report_states_reason_and_next_step():
    # Arrange / Act
    text = reports.error({"task": "t9", "message": "RuntimeError: tests failed"}, [], "Task t9 needs attention.")
    # Assert
    assert "Reason: RuntimeError: tests failed" in text and "resume task t9" in text


def test_search_tags_provider_and_falls_back(monkeypatch):
    # Arrange
    import websearch
    from websearch import Source

    def broken(q, n):
        raise OSError("down")

    monkeypatch.setattr(websearch, "providers", lambda: [("tavily", broken), ("duckduckgo", lambda q, n: [Source("T", "https://x.dev", "s")])])
    monkeypatch.setattr(websearch, "page_text", lambda url: "")
    # Act
    hits = websearch.search("q")
    # Assert
    assert hits[0]["provider"] == "duckduckgo"


def test_tavily_is_first_when_key_set(monkeypatch):
    # Arrange
    import websearch
    monkeypatch.setenv("TAVILY_API_KEY", "x")
    # Act
    names = [n for n, _ in websearch.providers()]
    # Assert
    assert names[0] == "tavily" and names[-1] == "duckduckgo"


def test_restart_uses_systemd_when_service_active(monkeypatch, capsys):
    # Arrange
    import daemon
    calls = []
    monkeypatch.setattr(daemon, "request", lambda path: {"pid": 1})
    monkeypatch.setattr(daemon, "service_active", lambda: True)
    monkeypatch.setattr(daemon.subprocess, "run", lambda cmd, check: calls.append(cmd))
    monkeypatch.setattr(daemon, "wait_healthy", lambda old, timeout=180.0: {"pid": 2})
    # Act
    daemon.restart()
    # Assert
    assert calls == [["systemctl", "--user", "restart", "fam.service"]] and "PID 2" in capsys.readouterr().out


def test_restart_without_service_stops_then_starts(monkeypatch):
    # Arrange
    import daemon
    order = []
    monkeypatch.setattr(daemon, "request", lambda path: (_ for _ in ()).throw(daemon.URLError("down")))
    monkeypatch.setattr(daemon, "service_active", lambda: False)
    monkeypatch.setattr(daemon, "stop", lambda: order.append("stop"))
    monkeypatch.setattr(daemon, "start", lambda: order.append("start"))
    monkeypatch.setattr(daemon, "wait_healthy", lambda old, timeout=180.0: {"pid": 3})
    # Act
    daemon.restart()
    # Assert
    assert order == ["stop", "start"]
