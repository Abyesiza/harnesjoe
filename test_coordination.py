"""Agents share information and never write the same thing twice (Arrange / Act / Assert)."""

import asyncio
import subprocess
from types import SimpleNamespace

from core import Bus, Node, Said, Task, on
from family import Lead
from mind import Brain


def lead(tmp_path):
    bus = Bus()
    return bus, Lead(bus, {"a": SimpleNamespace(price=1.0)}, Brain(["a"]), tmp_path / "home")


def test_notes_reach_a_running_task_once(tmp_path):
    _, lead_ = lead(tmp_path)
    lead_.tasks["t1"] = "build form"
    lead_.tell("t1", "sage", "use the existing form component")
    assert "- sage: use the existing form component" in lead_.heard("t1")
    assert lead_.heard("t1") == ""


def test_notes_for_unknown_tasks_are_ignored(tmp_path):
    _, lead_ = lead(tmp_path)
    lead_.tell("nope", "sage", "x")
    assert lead_.heard("nope") == ""


def test_a_repeated_request_is_passed_to_the_running_task_not_started_twice(tmp_path):
    bus, lead_ = lead(tmp_path)
    said = []

    class Spy(Node):
        @on(Said)
        async def s(self, e):
            said.append(e.text)

    Spy(bus)
    lead_.active["a"].add("t1")
    lead_.tasks["t1"] = "add a login form"
    lead_.where["t1"] = "/r"
    asyncio.run(bus.emit(Task("add a login form", cwd="/r", repo="/r", id="t2")))
    assert "t2" not in lead_.active["a"] and "t2" not in lead_.tasks
    assert "already running" in said[0] and "asked again" in lead_.heard("t1")


def test_overlapping_files_are_shared_between_running_tasks(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a, cwd=repo: subprocess.run(a, cwd=cwd, check=True, capture_output=True)
    run("git", "init", "-q", "-b", "main")
    (repo / "a.py").write_text("x = 0\n")
    run("git", "add", ".")
    run("git", "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", "i")
    _, lead_ = lead(tmp_path)
    for tid, value in (("t1", "1"), ("t2", "2")):
        tree = lead_.root / "work" / tid
        run("git", "worktree", "add", "-q", "-b", f"fam/{tid}", str(tree))
        (tree / "a.py").write_text(f"x = {value}\n")
        lead_.active["a"].add(tid)
        lead_.tasks[tid] = f"task {tid}"
        lead_.where[tid] = str(repo)
    mine = Task("task t1", cwd=str(lead_.root / "work" / "t1"), repo=str(repo), id="t1")
    more = asyncio.run(lead_.followup(mine, "ember", mine.cwd))
    assert "also changing a.py" in more and "x = 2" in more
    assert "also changes a.py" in lead_.heard("t2")
    assert asyncio.run(lead_.followup(mine, "ember", mine.cwd)) == ""
