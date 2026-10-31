import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import sys
import time
import uuid
from collections import Counter
from contextvars import ContextVar
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Protocol

import skills
from core import ACCESS, Access, Correction, Lesson, Node, jsonin, on, sh_split
from mind import enc

log = logging.getLogger("fam.agents")

PRICE = {"claude-haiku-5-5": 0.10, "claude-sonnet-5-5": 2.0, "claude-opus-5-5": 4.0}
RESERVE = float(os.environ.get("FAM_CALL_RESERVE", "0.5"))  # USD each running call holds against the cap


STRICT_JSON = "Your previous reply could not be parsed. Reply with exactly one JSON object and nothing else: no prose, no code fences, no second object."
# CLI diagnostics that must never reach a user-facing reply (they are logged instead).
WARNING = re.compile(r"^(Permission (deny|allow) rule\b.*|Warning:.*|.*mixes \* with the trailing :\* prefix syntax.*|.*matches no known tool.*)$")


HOOK_TIMEOUT = 20  # seconds; the default hook timeout is 10 minutes, so a stuck hook would stall the job


def strip_warnings(out: str) -> tuple[str, list[str]]:
    """Split CLI warning lines from the model's answer."""
    keep: list[str] = []
    warned: list[str] = []
    for line in out.splitlines():
        (warned if WARNING.match(line.strip()) else keep).append(line)
    return "\n".join(keep).strip("\n"), warned


def short_reason(cli: str, rc: int, err: str, out: str) -> str:
    """A short, human reason for a failed CLI call: the last meaningful stderr line, never the model's plan text."""
    lines = [l.strip() for l in err.splitlines() if l.strip() and not WARNING.match(l.strip())]
    if lines:
        return f"{cli} exited with {rc}: {lines[-1][:200]}"
    tail = [l.strip() for l in out.splitlines() if l.strip()]
    hint = next((l for l in reversed(tail) if re.search(r"(?i)error|denied|blocked|limit|not found|invalid|timed? ?out", l)), "")
    return f"{cli} exited with {rc}" + (f": {hint[:200]}" if hint else " (no error message; see the run log)")


class OverBudget(Exception):
    pass


class AgentError(ValueError):
    pass


class Ledger:
    """Model spend against a hard cap: exact when the CLI reports its cost, else estimated (tokens ≈ characters / 4).
    Each running call holds a reserve, so parallel calls cannot jointly overshoot. Every charge and refusal is
    appended to <ledger>.jsonl. A daily ledger starts from zero each local day; a run ledger never resets."""

    def __init__(s, cap, daily=True):
        s.cap, s.spent, s.held, s.path, s.daily, s.day = cap, 0.0, 0.0, None, daily, time.strftime("%Y-%m-%d")

    def attach(s, path) -> None:
        s.path = Path(path)
        try:
            data = json.loads(s.path.read_text())
            if data.get("day") == s.day or not s.daily:
                s.spent = float(data.get("spent", 0.0))
        except (OSError, ValueError, TypeError):
            pass

    def roll(s):
        if s.daily and (today := time.strftime("%Y-%m-%d")) != s.day:
            s.day, s.spent = today, 0.0

    def hold(s, usd, who=""):
        s.roll()
        if s.spent + s.held + usd > s.cap:
            s.record({"refused": who, "reserve": usd, "held": round(s.held, 4)})
            raise OverBudget(f"budget cap ${s.cap:.2f} reached: spent ${s.spent:.4f}, ${s.held:.2f} held by running calls")
        s.held += usd

    def charge(s, usd, held=0.0, who="", exact=False):
        s.roll()
        s.held = max(0.0, s.held - held)
        s.spent += usd
        s.record({"who": who, "usd": round(usd, 6), "exact": exact})
        if s.path:
            try:
                s.path.write_text(json.dumps({"day": s.day, "spent": s.spent, "cap": s.cap}))
            except OSError as e:
                log.warning("could not save the budget ledger: %s", e)

    def record(s, row):
        if not s.path:
            return
        try:
            with s.path.with_suffix(".jsonl").open("a") as f:
                f.write(json.dumps({"ts": time.time(), **row, "spent": round(s.spent, 6), "cap": s.cap}) + "\n")
        except OSError as e:
            log.warning("could not log spend: %s", e)

    @property
    def over(s):
        return s.spent >= s.cap


@dataclass
class Persona:
    name: str
    mission: str
    checks: tuple = ()
    voice: str = ""

    def render(s, rules=()):
        voice = [f"Voice: {s.voice}. Use it for anything meant for a human; keep code, JSON and commands exact."] if s.voice else []
        return "\n".join([f"# {s.name}", s.mission, *voice, *(f"- check: {c}" for c in s.checks), *(f"- rule: {r}" for r in rules)])


class Rules(Node):
    def __init__(s, bus, path, k=2):
        super().__init__(bus)
        s.path, s.k = Path(path), k
        d = json.loads(s.path.read_text()) if s.path.exists() else {}
        s.n, s.cand = Counter(d.get("n", {})), Counter(d.get("cand", {}))

    @property
    def active(s):
        return [r for r, c in s.n.items() if c >= s.k]

    def key(s, text):
        t = text.strip().lower()
        return next((r for r in (*s.n, *s.cand) if enc(r) @ enc(t) > 0.85 and SequenceMatcher(None, r, t).ratio() > 0.8), t)

    def save(s):
        s.path.parent.mkdir(parents=True, exist_ok=True)
        s.path.write_text(json.dumps({"n": s.n, "cand": s.cand}))

    @on(Correction)
    async def see(s, c):
        k = s.key(c.text)
        s.n[k] += s.k if k in s.cand else 1
        s.cand.pop(k, None)
        s.save()

    @on(Lesson)
    async def suggest(s, l):
        k = s.key(l.text)
        if k not in s.n:
            s.cand[k] += 1
        s.save()


class Tracker(Protocol):
    def start(self, cli: str, cwd: str, prompt: str) -> str: ...
    def finish(self, run_id: str, status: str, output: str, cli: str = "") -> None: ...
    def fallback(self, run_id: str, failed: str, to: str, reason: str) -> None: ...
    def command(self, run_id: str, entry: dict) -> None: ...


CURRENT_RUN: ContextVar[str] = ContextVar("fam_run", default="")
RUNLOG = Path(__file__).resolve().with_name("runlog.py")


def hook_settings() -> str:
    """Claude settings JSON that records every Bash call through runlog.py (it never blocks)."""
    hook = lambda stage: [{"matcher": "Bash", "hooks": [{"type": "command", "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(RUNLOG))} {stage}", "timeout": HOOK_TIMEOUT}]}]  # noqa: E731
    return json.dumps({"hooks": {"PreToolUse": hook("pre"), "PostToolUse": hook("post")}})


def merge_commands(lines: list[dict]) -> list[dict]:
    """Pair hook pre/post records into one entry per command: folder, command, exit code, duration, output."""
    out: dict[str, dict] = {}
    for i, e in enumerate(lines):
        key = e.get("use") or f"n{i}"
        row = out.setdefault(key, {"command": e.get("command", ""), "cwd": e.get("cwd", ""), "started": e.get("ts"), "exit": None, "output": "", "status": "running"})
        if e.get("stage") == "post":
            row.update(exit=e.get("exit"), output=str(e.get("output", ""))[-1000:], ended=e.get("ts"), status="done" if e.get("exit") in (0, None) else "failed")
            if row.get("started") is None:
                row["started"] = e.get("ts")
    for row in out.values():
        if row.get("ended") and row.get("started"):
            row["seconds"] = round(row["ended"] - row["started"], 2)
    return list(out.values())


class Agent:
    cmd: tuple[str, ...] = ()
    stdin = False  # prompt goes on stdin instead of argv (cmd.exe caps a command line at 8191 characters)
    price = 1.0
    wrap = tuple(os.environ.get("FAM_WRAP", "").split())
    limit = int(os.environ.get("FAM_MAX_AGENTS", "32"))  # high enough that chat never waits behind running tasks
    sem = asyncio.Semaphore(limit)
    ledger = Ledger(float(os.environ.get("FAM_BUDGET", "5")))
    timeout = float(os.environ.get("FAM_AGENT_TIMEOUT", "900"))
    tracker: Tracker | None = None
    skills_root = None  # set on members that should be offered skills
    profile = None  # what the family has learned about the user's way of working
    working: Counter = Counter()  # persona name -> calls in flight, so reviewers and advisors show as busy

    def __init__(s, persona, rules, model: str = "", fallbacks: list["Agent"] | None = None):
        s.persona, s.rules, s.model, s.price = persona, rules, model, PRICE.get(model, 1.0)
        s.fallbacks: list[Agent] = list(fallbacks or [])

    @property
    def cli(s) -> str:
        return s.cmd[0] if s.cmd else type(s).__name__.lower()

    def argv(s, prompt: str, mcp: str | None = None, live: bool = False) -> list[str]:
        return [*s.cmd, *s.access_args(ACCESS.get()), prompt]

    def access_args(s, access: Access) -> list[str]:
        return []

    def read(s, out: str) -> tuple[str, float | None]:
        """The answer text and the exact USD cost when the CLI reports one."""
        return out, None
    def prompt(s, text: str) -> str:
        rules = s.rules.active if s.rules else ()
        extra = skills.block(s.skills_root, text) if s.skills_root else ""
        about = s.profile.text(set(rules)) if s.profile else ""
        return f"{s.persona.render(rules)}\n\n{about}{extra}{text}"

    async def once(s, text: str, cwd: str = ".", mcp: str | None = None) -> str:
        prompt = s.prompt(text)
        home = Path(os.environ.get("FAM_HOME", Path.home() / ".fam")).expanduser() / "shell"
        home.mkdir(parents=True, exist_ok=True)
        trail = home / f"{CURRENT_RUN.get() or 'adhoc'}-{uuid.uuid4().hex[:6]}.jsonl"
        env = {**os.environ, "FAM_RUN_LOG": str(trail)}
        argv = [*s.wrap, *s.argv(prompt, mcp, live=True)]
        argv[0] = shutil.which(argv[0]) or argv[0]  # claude.cmd / copilot.cmd on Windows
        who = f"{s.persona.name} {s.cli} {s.model}".strip()
        out, err, usd = "", "", None
        async with s.sem:
            s.ledger.hold(RESERVE, who)
            tail = asyncio.create_task(s.follow(trail, CURRENT_RUN.get()))
            try:
                rc, out, err = await sh_split(*argv, cwd=cwd, input=prompt if s.stdin else None, timeout=s.timeout, env=env)
                out, warned = strip_warnings(out)
                out, usd = s.read(out)
            except TimeoutError as e:
                raise AgentError(f"{s.cli} timed out after {s.timeout:.0f}s") from e
            except OSError as e:
                raise AgentError(f"could not start {s.cli}: {e}") from e
            finally:
                tail.cancel()
                await s.flush(trail, CURRENT_RUN.get())
                s.ledger.charge(usd if usd is not None else s.price * (len(prompt) + 5 * len(out)) / 4e6, RESERVE, who, usd is not None)
        for line in [*warned, *err.splitlines()]:
            if line.strip():
                log.warning("%s: %s", s.cli, line.strip()[:300])
        if rc:
            raise AgentError(short_reason(s.cli, rc, err, out))
        if not out.strip():
            raise AgentError(f"{s.cli} returned no output")
        return out

    async def follow(s, log: Path, run_id: str) -> None:
        """Stream hook records to the tracker while the CLI is still running."""
        seen = 0
        while True:
            await asyncio.sleep(1)
            seen = s.emit(log, run_id, seen)

    async def flush(s, log: Path, run_id: str) -> None:
        s.emit(log, run_id, 0, final=True)
        log.unlink(missing_ok=True)

    def emit(s, log: Path, run_id: str, seen: int, final: bool = False) -> int:
        if not log.exists() or not Agent.tracker or not run_id:
            return seen
        lines = []
        for raw in log.read_text().splitlines():
            try:
                lines.append(json.loads(raw))
            except ValueError:
                continue
        if final or len(lines) > seen:
            for row in merge_commands(lines):
                Agent.tracker.command(run_id, {**row, "cli": s.cli, "agent": s.persona.name})
        return len(lines)

    async def ask(s, text: str, cwd: str = ".", mcp: str | None = None) -> str:
        who = getattr(s.persona, "name", "")
        Agent.working[who] += 1
        try:
            return await s.ask_chain(text, cwd, mcp)
        finally:
            Agent.working[who] -= 1

    async def ask_chain(s, text: str, cwd: str = ".", mcp: str | None = None) -> str:
        if s.ledger.over:
            raise OverBudget(s.ledger.spent)
        tracker = Agent.tracker
        run_id = tracker.start(s.cli, str(cwd), text) if tracker else ""
        CURRENT_RUN.set(run_id)
        chain = [s, *s.fallbacks]
        for i, agent in enumerate(chain):
            try:
                out = await agent.once(text, cwd, mcp)
            except AgentError as e:
                if i + 1 < len(chain):
                    if tracker:
                        tracker.fallback(run_id, agent.cli, chain[i + 1].cli, str(e)[:300])
                    continue
                if tracker:
                    tracker.finish(run_id, "failed", str(e), agent.cli)
                raise
            if tracker:
                tracker.finish(run_id, "done", out, agent.cli)
            return out
        raise AgentError("no agent configured")

    async def ask_json(s, text: str, cwd: str = ".", mcp: str | None = None) -> dict:
        out = await s.ask(text, cwd, mcp)
        try:
            return jsonin(out)
        except ValueError:
            # One retry with a stricter instruction; a second failure is a real error.
            log.warning("%s returned no parseable JSON; retrying once", s.cli)
            return jsonin(await s.ask(f"{text}\n\n{STRICT_JSON}", cwd, mcp))


# Inspection-only tools pre-approved for read-only jobs so `-p` never stalls on a permission prompt.
READ_SHELL = ("ls", "cd", "pwd", "cat", "head", "tail", "tree", "du", "wc", "stat", "file", "git status", "git log", "git branch", "git diff", "git remote", "git show", "git rev-parse")
CLAUDE_READ = ("Read", "Glob", "Grep", "LS", *(f"Bash({c}:*)" for c in READ_SHELL), "Bash(git -C:*)")
CLAUDE_WRITE = ("Edit", "Write", "NotebookEdit")
# Primary-source lookups for research; withheld when reviewing untrusted code.
WEB_SHELL = ("gh search", "gh repo view", "gh release view")
CLAUDE_WEB = ("WebSearch", "WebFetch", *(f"Bash({c}:*)" for c in WEB_SHELL))


class ClaudeCode(Agent):
    cmd = ("claude", "-p")
    stdin = True

    def access_args(s, access: Access) -> list[str]:
        out = [a for d in access.dirs for a in ("--add-dir", d)]
        if access.readonly:
            out += ["--allowedTools", *CLAUDE_READ, *([] if access.untrusted else CLAUDE_WEB), "--disallowedTools", *CLAUDE_WRITE]
        return out

    def argv(s, prompt, mcp=None, live=False):
        access = ACCESS.get()
        run = ["--settings", hook_settings(), *([] if access.readonly else ["--dangerously-skip-permissions"])] if live else []
        return [
            *s.cmd,
            "--output-format",
            "json",
            *(["--model", s.model] if s.model else []),
            *(["--strict-mcp-config", "--mcp-config", mcp] if mcp else []),
            *s.access_args(access),
            *run,
        ]

    def read(s, out):
        for raw in (out, *reversed(out.splitlines())):
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if isinstance(d, dict) and "result" in d:
                cost = d.get("total_cost_usd")
                return str(d.get("result") or ""), float(cost) if cost is not None else None
        return out, None


class Copilot(Agent):
    cmd = ("copilot", "-p")

    def argv(s, prompt: str, mcp: str | None = None, live: bool = False) -> list[str]:
        # -p takes the prompt as its value; options follow it.
        access = ACCESS.get()
        return [*s.cmd, prompt, *s.access_args(access), *(["--allow-all"] if live and not access.readonly else [])]

    def access_args(s, access: Access) -> list[str]:
        out = [a for d in access.dirs for a in ("--add-dir", d)]
        if access.readonly:
            out += [a for c in (*READ_SHELL, *([] if access.untrusted else WEB_SHELL)) for a in ("--allow-tool", f"shell({c})")]
            out += ["--deny-tool", "write"]
        return out
