"""Regression tests for the bugs in Joel's chat log (Oct 2026). Arrange / Act / Assert."""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest

import agents
import core
import server
import skipper
from agents import Ledger, short_reason, strip_warnings
from skipper import Skipper, StateStore as Store


class Persona(SimpleNamespace):
    pass


def app_with(cast):
    lead = SimpleNamespace(root="/nonexistent", members=[], active={}, completed={}, reward={})
    return SimpleNamespace(cast=cast, lead=lead, linker=SimpleNamespace(props={}, trusted={}))


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return Store(tmp_path / "state.sqlite3")


def test_job_id_question_returns_that_jobs_report(store):
    # Arrange
    store.message("skipper", "Quill — research job dd7dd618 finished (2 round(s)).\nResult: metis is a DB", "dd7dd618")
    store.message("skipper", "ℹ️ Quill read /x (read-only)", "dd7dd618")
    # Act
    answer = Skipper(store).direct_answer("whats the result of dd7dd618?", app_with({}))
    # Assert
    assert answer and answer[0].startswith("Quill — research job dd7dd618") and answer[1] == "dd7dd618"


def test_what_member_found_returns_latest_report(store):
    # Arrange
    store.message("skipper", "Quill — research job aaaa1111 finished.\nResult: old", "aaaa1111")
    store.message("skipper", "Quill — research job bbbb2222 finished.\nResult: new", "bbbb2222")
    cast = {"quill": SimpleNamespace(persona=Persona(name="Quill"))}
    # Act
    answer = Skipper(store).direct_answer("what quill found", app_with(cast))
    # Assert
    assert answer and "Result: new" in answer[0]


def test_model_question_answered_from_config(store):
    # Arrange
    fallback = SimpleNamespace(cli="claude", model="claude-haiku-5-5")
    spark = SimpleNamespace(persona=Persona(name="Spark"), cli="copilot", model="", fallbacks=[fallback])
    # Act
    answer = Skipper(store).direct_answer("which model does spark use", app_with({"spark": spark}))
    # Assert
    assert answer and "copilot → claude (claude-haiku-5-5)" in answer[0]


@pytest.mark.parametrize("raw", ['```json\n{"a": 1}\n```', 'Sure! {"a": 1} and also {"b": 2}', '{"a": 1}\nHope that helps.'])
def test_jsonin_returns_first_valid_object(raw):
    # Act
    out = core.jsonin(raw)
    # Assert
    assert out == {"a": 1}


def test_ask_json_retries_once_with_strict_instruction(monkeypatch):
    # Arrange
    replies = iter(["not json at all", '{"ok": true}'])
    seen = []

    class A(agents.Agent):
        cli = "fake"
        async def ask(s, text, cwd=".", mcp=None):
            seen.append(text)
            return next(replies)
    a = A.__new__(A)
    # Act
    out = asyncio.run(a.ask_json("plan"))
    # Assert
    assert out == {"ok": True} and len(seen) == 2 and agents.STRICT_JSON in seen[1]


def test_error_reason_is_stderr_not_plan_text():
    # Arrange
    plan = "## Plan\n1. edit app.py\n2. run tests\n" * 20
    # Act
    why = short_reason("claude", 1, "Error: rate limit reached\n", plan)
    # Assert
    assert why.startswith("claude") and "rate limit" in why and "## Plan" not in why and len(why) < 200


def test_cli_warnings_are_stripped_from_replies():
    # Arrange
    out = "Permission deny rule \"Bash(rm -rf *:*)\" mixes * with the trailing :* prefix syntax\nWarning: MultiEdit matches no known tool\nThe answer."
    # Act
    clean, warned = strip_warnings(out)
    # Assert
    assert clean == "The answer." and len(warned) == 2


def test_member_chat_without_folder_uses_neutral_lobby(tmp_path, monkeypatch):
    # Arrange
    monkeypatch.setenv("HOME", str(tmp_path))
    # Act
    where = skipper.lobby()
    # Assert
    assert ".fam" not in where and where.endswith("lobby")


def test_fam_home_gitignore_covers_secrets_state_and_logs(tmp_path):
    # Arrange
    (tmp_path / ".gitignore").write_text("custom\n")
    # Act
    server.protect_home(tmp_path)
    # Assert
    lines = (tmp_path / ".gitignore").read_text().splitlines()
    assert {"custom", "token", "state.sqlite3", "*.log"} <= set(lines)


def test_local_project_name_found_before_web(tmp_path, monkeypatch):
    # Arrange
    monkeypatch.setenv("HOME", str(tmp_path))
    proj = tmp_path / "Desktop" / "my_projects" / "metisdb"
    (proj / ".git").mkdir(parents=True)
    # Act
    found = skipper.local_projects("lets do a read through the metis project foldr", [])
    # Assert
    assert found and found[0] == str(proj)


def test_folders_for_prefers_approved_folder_names(store, tmp_path):
    # Arrange
    approved = tmp_path / "work" / "metis"
    approved.mkdir(parents=True)
    store.grant_folder(str(approved))
    # Act
    found = Skipper(store).folders_for("read through the metis project")
    # Assert
    assert found == [str(approved)]


def test_ledger_survives_restart_and_resets_daily(tmp_path):
    # Arrange
    path = tmp_path / "ledger.json"
    first = Ledger(5.0)
    first.attach(path)
    first.charge(1.0, 1000, 1000)
    # Act
    second = Ledger(5.0)
    second.attach(path)
    path.write_text(json.dumps({"day": "2000-01-01", "spent": 9}))
    third = Ledger(5.0)
    third.attach(path)
    # Assert
    assert second.spent == pytest.approx(first.spent) and second.spent > 0 and third.spent == 0


def test_report_lists_reads_outside_approved_folders():
    # Arrange
    import reports
    runs = [{"cli": "claude", "status": "done", "started": 0, "ended": 1, "fallbacks": [],
             "commands": [{"command": "ls /a", "exit": 0, "status": "done", "outside": ["/a"]},
                          {"command": "Read /b/x", "status": "read", "outside": ["/b/x"]}]}]
    # Act
    line = reports.run_line(runs)
    # Assert
    assert "Permissions: read outside approved folders: /a, /b/x" in line and "Read /b/x" not in line


def test_guard_hook_blocks_secret_reads_and_logs_outside_reads(tmp_path, monkeypatch):
    # Arrange
    import famguard
    monkeypatch.setenv("HOME", str(tmp_path))
    log = tmp_path / "guard.jsonl"
    monkeypatch.setenv("FAM_RUN_LOG", str(log))
    monkeypatch.setenv("FAM_GUARD_DIRS", str(tmp_path / "repo"))
    monkeypatch.setenv("FAM_GUARD_MODE", "work")
    (tmp_path / "other").mkdir()
    # Act
    secret = famguard.main("pre", json.dumps({"tool_name": "Read", "tool_input": {"file_path": str(tmp_path / ".ssh/id_rsa")}, "cwd": str(tmp_path)}))
    fine = famguard.main("pre", json.dumps({"tool_name": "Read", "tool_input": {"file_path": str(tmp_path / "other/a.py")}, "cwd": str(tmp_path)}))
    # Assert
    rows = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    assert secret == 2 and fine == 0 and any(r.get("outside") for r in rows)
