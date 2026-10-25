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


# ---- CLI fallback --------------------------------------------------------

def run_agent(monkeypatch, responses):
    calls = []

    async def fake_sh(*argv, cwd=None, input=None, timeout=None):
        calls.append(argv[0])
        result = responses[argv[0]]
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(agents, "sh", fake_sh)
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
    assert ClaudeCode(Persona(), None).argv("x") == ["claude", "-p", "x"]


def test_research_job_runs_in_named_folder_with_read_only_access_on_fallback(monkeypatch, spaced):
    # Arrange
    calls = []

    async def fake_sh(*argv, cwd=None, input=None, timeout=None):
        calls.append((list(argv), cwd))
        if argv[0] == "copilot":
            return 1, "denied"
        return 0, '{"candidates": {"app": 1.0}, "gaps": [], "say": "found app", "confidence": 0.9}'

    monkeypatch.setattr(agents, "sh", fake_sh)
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
