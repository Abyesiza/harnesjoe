"""Command logging hook and run records (Arrange / Act / Assert)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import agents
from agents import merge_commands
from reports import commands_block
from skipper import StateStore


def _hook(stage, event, env):
    return subprocess.run([sys.executable, str(Path(agents.RUNLOG)), stage],
                          input=json.dumps(event), capture_output=True, text=True, env={**os.environ, **env})


def test_hook_logs_and_never_blocks(tmp_path):
    log = tmp_path / "run.jsonl"
    env = {"FAM_RUN_LOG": str(log)}
    event = {"tool_name": "Bash", "tool_input": {"command": "sudo ls"}, "cwd": str(tmp_path), "tool_use_id": "a"}
    pre = _hook("pre", event, env)
    post = _hook("post", {**event, "tool_response": {"stdout": "ok"}}, env)
    rows = merge_commands([json.loads(x) for x in log.read_text().splitlines()])
    assert pre.returncode == 0 and post.returncode == 0
    assert [r["status"] for r in rows] == ["done"]


def test_hook_ignores_bad_input():
    import runlog
    assert runlog.run("pre", "{not json") == 0


def test_hook_redacts_secrets_in_the_run_log(tmp_path, monkeypatch):
    import runlog
    log = tmp_path / "run.jsonl"
    monkeypatch.setenv("FAM_RUN_LOG", str(log))
    runlog._log({"command": "curl -H 'Authorization: Bearer abcdef1234567890xyz' -d api_key=supersecret99 x sk-ABCDEFGHIJKLMNOPQRSTUV", "output": "ghp_" + "a" * 36})
    entry = json.loads(log.read_text())
    assert "abcdef1234567890xyz" not in entry["command"] and "supersecret99" not in entry["command"] and "sk-ABCD" not in entry["command"]
    assert entry["output"] == "[redacted]"
    assert "api_key=[redacted]" in entry["command"]


def test_hook_settings_carry_a_timeout():
    hooks = json.loads(agents.hook_settings())["hooks"]["PreToolUse"][0]["hooks"][0]
    assert hooks["timeout"] == agents.HOOK_TIMEOUT


def test_task_runs_have_no_permission_limits():
    claude = agents.ClaudeCode(agents.Persona("x", "m"), None).argv("go", live=True)
    copilot = agents.Copilot(agents.Persona("x", "m"), None).argv("go", live=True)
    assert "--dangerously-skip-permissions" in claude and "--disallowedTools" not in claude
    assert "--allow-all-tools" in copilot and "--deny-tool" not in copilot


def test_store_and_report_carry_commands(tmp_path):
    store = StateStore(tmp_path / "s.sqlite3")
    run = store.run_start("claude", str(tmp_path), "hi")
    entry = {"command": "git status", "started": 1.0, "exit": 0, "seconds": 0.2, "status": "done"}
    store.run_command(run["id"], entry)
    store.run_command(run["id"], entry)
    runs = store.runs()
    text = commands_block(runs[0]["commands"])
    assert len(runs[0]["commands"]) == 1
    assert "Commands run (1)" in text and "git status (exit 0, 0.2s)" in text
