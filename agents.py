import asyncio
import json
import os
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Protocol

from core import ACCESS, Access, Correction, Lesson, Node, jsonin, on, sh
from mind import enc

PRICE = {"claude-haiku-5-5": 0.10, "claude-sonnet-5-5": 2.0, "claude-opus-5-5": 4.0}


class OverBudget(Exception):
    pass


class AgentError(ValueError):
    pass


class Ledger:
    def __init__(s, cap):
        s.cap, s.spent = cap, 0.0

    def charge(s, price, tin, tout):
        s.spent += (tin + 5 * tout) * price / 1e6

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


class Agent:
    cmd: tuple[str, ...] = ()
    price = 1.0
    wrap = tuple(os.environ.get("FAM_WRAP", "").split())
    sem = asyncio.Semaphore(4)
    ledger = Ledger(float(os.environ.get("FAM_BUDGET", "5")))
    timeout = float(os.environ.get("FAM_AGENT_TIMEOUT", "900"))
    tracker: Tracker | None = None

    def __init__(s, persona, rules, model: str = "", fallbacks: list["Agent"] | None = None):
        s.persona, s.rules, s.model, s.price = persona, rules, model, PRICE.get(model, 1.0)
        s.fallbacks: list[Agent] = list(fallbacks or [])

    @property
    def cli(s) -> str:
        return s.cmd[0] if s.cmd else type(s).__name__.lower()

    def argv(s, prompt: str, mcp: str | None = None) -> list[str]:
        return [*s.cmd, *s.access_args(ACCESS.get()), prompt]

    def access_args(s, access: Access) -> list[str]:
        return []

    def prompt(s, text: str) -> str:
        rules = s.rules.active if s.rules else ()
        return f"{s.persona.render(rules)}\n\n{text}"

    async def once(s, text: str, cwd: str = ".", mcp: str | None = None) -> str:
        prompt = s.prompt(text)
        argv = [*s.wrap, *s.argv(prompt, mcp)]
        async with s.sem:
            try:
                rc, out = await sh(*argv, cwd=cwd, timeout=s.timeout)
            except TimeoutError as e:
                raise AgentError(f"{s.cli} timed out after {s.timeout:.0f}s") from e
            except OSError as e:
                raise AgentError(f"could not start {s.cli}: {e}") from e
        if rc:
            raise AgentError(out.strip()[-500:] or f"{s.cli} exited with {rc}")
        if not out.strip():
            raise AgentError(f"{s.cli} returned no output")
        s.ledger.charge(s.price, len(prompt) / 4, len(out) / 4)
        return out

    async def ask(s, text: str, cwd: str = ".", mcp: str | None = None) -> str:
        if s.ledger.over:
            raise OverBudget(s.ledger.spent)
        tracker = Agent.tracker
        run_id = tracker.start(s.cli, str(cwd), text) if tracker else ""
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
        return jsonin(await s.ask(text, cwd, mcp))


# Inspection-only tools pre-approved for read-only jobs so `-p` never stalls on a permission prompt.
READ_SHELL = ("ls", "cd", "pwd", "cat", "head", "tail", "tree", "du", "wc", "stat", "file", "git status", "git log", "git branch", "git diff", "git remote", "git show", "git rev-parse")
CLAUDE_READ = ("Read", "Glob", "Grep", "LS", *(f"Bash({c}:*)" for c in READ_SHELL), "Bash(git -C:*)")
CLAUDE_WRITE = ("Edit", "Write", "MultiEdit", "NotebookEdit")


class ClaudeCode(Agent):
    cmd = ("claude", "-p")

    def access_args(s, access: Access) -> list[str]:
        out = [a for d in access.dirs for a in ("--add-dir", d)]
        if access.readonly:
            out += ["--allowedTools", *CLAUDE_READ, "--disallowedTools", *CLAUDE_WRITE]
        return out

    def argv(s, prompt, mcp=None):
        return [
            *s.cmd,
            *(["--model", s.model] if s.model else []),
            *(["--strict-mcp-config", "--mcp-config", mcp] if mcp else []),
            *(access := s.access_args(ACCESS.get())),
            # Variadic flags such as --allowedTools would otherwise swallow the prompt.
            *(["--"] if access else []),
            prompt,
        ]


class Copilot(Agent):
    cmd = ("copilot", "-p")

    def argv(s, prompt: str, mcp: str | None = None) -> list[str]:
        # -p takes the prompt as its value; options follow it.
        return [*s.cmd, prompt, *s.access_args(ACCESS.get())]

    def access_args(s, access: Access) -> list[str]:
        out = [a for d in access.dirs for a in ("--add-dir", d)]
        if access.readonly:
            out += [a for c in READ_SHELL for a in ("--allow-tool", f"shell({c})")]
            out += ["--deny-tool", "write"]
        return out
