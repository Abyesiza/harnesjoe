"""One-command backlog run: python run.py <repo> [--budget 5] [--parallel 3]

Plans the whole backlog once (overlaps, hidden dependencies, contradictions), runs tickets through the
family in parallel worktrees, merges accepted work onto one integration branch in a recorded order, and
decides "done" only by rerunning each ticket's acceptance test on the final tree. Logs land in
~/.fam/runs/<stamp>/: events.jsonl, runs.jsonl, spend.jsonl, merges.jsonl, plan.json, report.json.
"""

import argparse
import asyncio
import contextlib
import json
import os
import re
import shutil
import time
import uuid
from collections import defaultdict
from dataclasses import asdict, is_dataclass
from pathlib import Path

import core
from agents import Agent, Ledger, OverBudget
from app import build
from core import ACCESS, CURRENT_TASK, Access, Applied, Node, Task, Tick, jsonin, on, sh

LOCKED = "test/,acceptance/,scripts/,backlog/,package.json,tests.lock.json"
PLAN = (
    "You plan a backlog for several coding agents working in parallel. Read the repository (read-only) and every ticket. "
    "Tickets may edit the same files, depend on each other without saying so, contradict each other, or describe work that "
    "sounds finished but is not. Reply only JSON: "
    '{"tickets": {"<id>": {"files": ["<repo path it will likely edit>"], "after": ["<id that must be merged first>"], '
    '"skip": "<empty, or why it must not be built>"}}}. '
    "For every pair of contradicting tickets keep exactly one; set skip on the other, naming the winner and the reason. "
    "Never skip a ticket only because it sounds done.\n"
)
SHOW = {"Assigned", "Decision", "Applied", "ApplyPending", "Blocked", "Question", "Said", "HumanDecision"}
WHY = SHOW | {"Feedback"}


def stamp():
    return time.strftime("%H:%M:%S")


class Log:
    """Append-only JSONL files, one per stream."""

    def __init__(s, root):
        s.root = root

    def __call__(s, stream, row):
        with (s.root / f"{stream}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), **row}, default=str) + "\n")


class Runs:
    """Agent.tracker: every CLI call with its fallbacks and shell commands."""

    def __init__(s, log):
        s.log = log

    def start(s, cli, cwd, prompt):
        rid = uuid.uuid4().hex[:10]
        s.log("runs", {"run": rid, "event": "start", "task": CURRENT_TASK.get(), "cli": cli, "cwd": cwd, "prompt": " ".join(prompt.split())[:300]})
        return rid

    def finish(s, rid, status, output, cli=""):
        s.log("runs", {"run": rid, "event": status, "cli": cli, "output": " ".join(output.split())[-600:]})

    def fallback(s, rid, failed, to, reason):
        s.log("runs", {"run": rid, "event": "fallback", "failed": failed, "to": to, "reason": reason})

    def command(s, rid, entry):
        s.log("runs", {"run": rid, "event": "command", **entry})


class Tap(Node):
    """Writes every family event to events.jsonl and keeps the latest word on each task."""

    def __init__(s, bus, log):
        super().__init__(bus)
        s.log, s.last = log, {}
        for cls in vars(core).values():
            if isinstance(cls, type) and is_dataclass(cls) and cls is not Tick:
                bus.subs[cls].append(s.tap(cls.__name__))

    def tap(s, kind):
        async def handle(e):
            d = asdict(e)
            s.log("events", {"type": kind, "data": d})
            t = d.get("task")
            tid = t.get("id") if isinstance(t, dict) else ""
            text = d.get("reason") or d.get("need") or d.get("text") or ""
            if kind == "Said" and (m := re.match(r"Task (\S+) ", text)):
                tid = m.group(1)
            if tid and kind in WHY:
                s.last[tid] = " ".join(f"{kind} ({d.get('source') or d.get('name') or d.get('member') or ''}): {text}".split())[:300]
            if kind in SHOW:
                who = d.get("member") or d.get("name") or ""
                print(f"{stamp()} {kind:12} {tid:14} {who} {str(text)[:140]}", flush=True)

        return handle


class Merges(Node):
    """Commits each applied task onto the integration branch, so later worktrees build on it."""

    def __init__(s, bus, tree, lead, tickets, log):
        super().__init__(bus)
        s.tree, s.lead, s.tickets, s.log, s.n = tree, lead, tickets, log, 0

    @on(Applied)
    async def commit(s, a):
        async with s.lead.lock:
            _, base = await sh("git", "rev-parse", "HEAD", cwd=s.tree)
            await sh("git", "add", "-A", cwd=s.tree)
            _, files = await sh("git", "diff", "--cached", "--name-only", cwd=s.tree)
            ticket = s.tickets.get(a.task.id, a.task.id)
            rc, out = await sh("git", "-c", "user.name=fam", "-c", "user.email=fam@localhost", "commit", "-q", "-m", f"fam: {ticket} ({a.task.id})", cwd=s.tree)
            _, head = await sh("git", "rev-parse", "HEAD", cwd=s.tree)
        s.n += not rc
        s.log("merges", {"order": s.n if not rc else None, "ticket": ticket, "task": a.task.id, "base": base.strip(), "head": head.strip(), "files": files.split(), "committed": not rc, "note": "" if not rc else out.strip()[:300]})


async def accepts(tree, ticket):
    f = Path(tree) / "acceptance" / f"{ticket}.test.js"
    if not f.exists():
        return None, ""
    rc, out = await sh("node", "--test", str(f.relative_to(tree)).replace("\\", "/"), cwd=tree)
    return rc == 0, out[-1500:]


async def npm(tree, *args):
    pkg = Path(tree) / "package.json"
    exe = shutil.which("npm")
    if not exe or not pkg.exists() or args[-1] not in json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {}) | {"test": 1}:
        return None, ""
    rc, out = await sh(exe, *args, cwd=tree)
    return rc == 0, out[-3000:]


def acyclic(plan, ids):
    """The plan's dependency edges, minus any that would close a cycle (a cycle would deadlock the run)."""
    after = {i: [] for i in ids}

    def reaches(a, b):
        stack, seen = [a], set()
        while stack:
            x = stack.pop()
            if x == b:
                return True
            if x not in seen:
                seen.add(x)
                stack += after[x]
        return False

    for i in ids:
        spec = plan.get(i) if isinstance(plan.get(i), dict) else {}
        for d in spec.get("after", []):
            if d in after and d != i and not reaches(d, i):
                after[i].append(d)
    return after


async def plan(app, tree, backlog):
    token = ACCESS.set(Access((tree,), readonly=True, untrusted=True))
    try:
        r = await app.skipper.ask(f"{PLAN}\nBacklog: {json.dumps(backlog['tasks'])}", tree)
        return jsonin(r).get("tickets", {})
    except (OverBudget, ValueError, KeyError) as e:
        print(f"{stamp()} planner failed ({type(e).__name__}: {e}); running every ticket with no plan")
        return {}
    finally:
        ACCESS.reset(token)


async def main(a):
    repo = Path(a.repo).expanduser().resolve()
    backlog = json.loads((Path(a.backlog) if a.backlog else repo / "backlog" / "backlog.json").read_text(encoding="utf-8"))
    home = Path(os.environ.get("FAM_HOME", Path.home() / ".fam")).expanduser()
    run = home / "runs" / time.strftime("%Y%m%d-%H%M%S")
    run.mkdir(parents=True)
    log, tree, branch = Log(run), str(run / "tree"), f"fam/run-{run.name}"
    rc, out = await sh("git", "worktree", "add", "-q", "-b", branch, tree, "HEAD", cwd=str(repo))
    if rc:
        raise SystemExit(f"could not create the integration worktree: {out}")

    Agent.ledger = Ledger(a.budget, daily=False)
    Agent.ledger.attach(run / "spend.json")
    Agent.tracker = Runs(log)
    locked = tuple(p.strip() for p in a.locked.split(",") if p.strip())
    app = build(home, apply_accepted=True, batch=True, locked=locked)
    tap = Tap(app.bus, log)
    tickets = {}
    Merges(app.bus, tree, app.lead, tickets, log)
    log("events", {"type": "RunStart", "data": {"repo": str(repo), "branch": branch, "budget": a.budget, "parallel": a.parallel, "tickets": [t["id"] for t in backlog["tasks"]]}})
    print(f"{stamp()} run {run.name}: {len(backlog['tasks'])} tickets, budget ${a.budget:.2f}, parallel {a.parallel}, branch {branch}")

    p = await plan(app, tree, backlog)
    (run / "plan.json").write_text(json.dumps(p, indent=1))
    known = [t["id"] for t in backlog["tasks"]]
    after = acyclic(p, known)
    done = {i: asyncio.Event() for i in known}
    files, gate, notes = defaultdict(asyncio.Lock), asyncio.Semaphore(a.parallel), {}

    async def ticket(tk):
        tid, spec = tk["id"], p.get(tk["id"], {}) if isinstance(p.get(tk["id"]), dict) else {}
        try:
            for dep in after[tid]:
                await done[dep].wait()
            if skip := str(spec.get("skip") or "").strip():
                notes[tid] = f"not built by plan: {skip}"
                return
            async with contextlib.AsyncExitStack() as stack:
                for f in sorted({str(x) for x in spec.get("files", [])}):
                    await stack.enter_async_context(files[f])
                await stack.enter_async_context(gate)
                ok, _ = await accepts(tree, tid)
                if ok:
                    notes[tid] = "acceptance already passed before any work"
                    return
                verify = f"node --test acceptance/{tid}.test.js" if (Path(tree) / "acceptance" / f"{tid}.test.js").exists() else ""
                body = f"{tk['title']}\n\n{tk['body']}\n\nNever edit these paths: {', '.join(locked)}. Done means `{verify or 'the regression suite'}` and the regression suite pass."
                t = Task(body, cwd=tree, repo=tree, flow="quick", verify=verify, id=f"{tid.lower()}-{uuid.uuid4().hex[:4]}")
                tickets[t.id] = tid
                CURRENT_TASK.set(t.id)
                try:
                    await app.bus.emit(t)
                except OverBudget as e:
                    notes[tid] = f"stopped by the budget guard: {e}"
                except Exception as e:  # noqa: BLE001 - one ticket's failure must not stop the run
                    notes[tid] = f"{type(e).__name__}: {e}"[:300]
                while pending := [j for j in app.steward.jobs if j.get_name() == t.id]:
                    await asyncio.wait(pending)
                notes.setdefault(tid, tap.last.get(t.id, ""))
        finally:
            done[tid].set()

    await asyncio.gather(*(ticket(tk) for tk in backlog["tasks"]))

    reg, reg_out = await npm(tree, "test")
    locked_ok, _ = await npm(tree, "run", "verify:tests")
    rows = []
    for tk in backlog["tasks"]:
        tid = tk["id"]
        ok, out = await accepts(tree, tid)
        skipped = notes.get(tid, "").startswith("not built by plan")
        claim = bool(ok) and reg is not False and locked_ok is not False and not skipped
        status = "done" if claim else "passes, not claimed (contradiction)" if ok and skipped else "not done"
        why = "" if claim else notes.get(tid) or ("acceptance fails on the final tree" if ok is False else "no acceptance test")
        if ok and not claim and not skipped:
            why = "acceptance passes, but the regression suite or locked-file check fails"
        rows.append({"id": tid, "title": tk["title"], "status": status, "acceptance": ok, "why": why, "evidence": "" if ok else out[-600:]})
    report = {
        "branch": branch, "tree": tree, "regression": reg, "locked_files_unchanged": locked_ok,
        "spent_usd": round(Agent.ledger.spent, 4), "cap_usd": a.budget, "tickets": rows,
        "clis": sorted({json.loads(x).get("cli") for x in (run / "runs.jsonl").read_text().splitlines() if '"start"' in x}) if (run / "runs.jsonl").exists() else [],
    }
    (run / "report.json").write_text(json.dumps(report, indent=1))
    log("events", {"type": "RunEnd", "data": {k: v for k, v in report.items() if k != "tickets"}})
    print(f"\nRegression suite: {'pass' if reg else 'FAIL' if reg is False else 'n/a'}   Locked files unchanged: {locked_ok}   Spend ${Agent.ledger.spent:.4f} of ${a.budget:.2f}")
    for r in rows:
        print(f"{r['id']:6} {r['status']:36} {r['title'][:50]:50} {r['why'][:120]}")
    print(f"\nDone: {sum(r['status'] == 'done' for r in rows)}/{len(rows)}. Work is committed on {branch} ({tree}). Logs: {run}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repo")
    ap.add_argument("--budget", type=float, default=float(os.environ.get("FAM_BUDGET", "5")), help="hard USD cap for this run")
    ap.add_argument("--parallel", type=int, default=int(os.environ.get("FAM_PARALLEL", "3")), help="tickets in flight at once")
    ap.add_argument("--backlog", default="", help="backlog JSON (default <repo>/backlog/backlog.json)")
    ap.add_argument("--locked", default=os.environ.get("FAM_LOCKED", LOCKED), help="comma-separated paths agents may never edit")
    asyncio.run(main(ap.parse_args()))
