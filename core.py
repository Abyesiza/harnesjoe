import asyncio
import inspect
import json
import logging
import re
import uuid
from asyncio.subprocess import PIPE, STDOUT
from collections import defaultdict
from contextvars import ContextVar
from dataclasses import dataclass, field

log = logging.getLogger("fam.core")
# Lets CLI runs know which task or research job started them without threading ids through every call.
CURRENT_TASK: ContextVar[str] = ContextVar("fam_task", default="")


@dataclass(frozen=True)
class Access:
    """Extra folders a CLI may touch, and whether it must stay read-only."""

    dirs: tuple[str, ...] = ()
    readonly: bool = False
    untrusted: bool = False  # untrusted code: inspect only, never execute it (no tests/builds)


ACCESS: ContextVar[Access] = ContextVar("fam_access", default=Access())  # noqa: B039 - frozen, immutable


@dataclass
class Task:
    text: str
    cwd: str = "."
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    tries: int = 0
    repo: str = ""
    pin: str = ""
    flow: str = ""


@dataclass
class Briefed:
    task: Task
    brief: dict
    cached: bool


@dataclass
class Output:
    task: Task
    member: str
    text: str
    check: str = ""


@dataclass
class Green:
    task: Task
    member: str
    check: str = ""


@dataclass
class Vote:
    task: Task
    member: str
    voter: str
    p: float
    veto: bool


@dataclass
class Decision:
    task: Task
    member: str
    accept: bool
    escalate: bool
    spread: float


@dataclass
class Outcome:
    task: Task
    member: str
    reward: float
    weight: float = 1.0


@dataclass
class Correction:
    text: str


@dataclass
class Lesson:
    text: str
    by: str = ""


@dataclass
class ToolAnswer:
    text: str
    ok: bool


@dataclass
class ResearchRequest:
    question: str
    folders: list = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


@dataclass
class Finding:
    question: str
    candidates: dict
    rounds: int
    reason: str
    say: str = ""
    id: str = ""
    sources: list = field(default_factory=list)


@dataclass
class Searched:
    id: str
    query: str
    sources: list


@dataclass
class Activity:
    source: str
    text: str
    level: str = "info"


@dataclass
class Fallback:
    run: str
    failed: str
    to: str
    reason: str


@dataclass
class Focus:
    field: str


@dataclass
class Scouted:
    repo: str
    score: float
    skills: list
    lessons: list
    say: str = ""


@dataclass
class Said:
    name: str
    text: str


@dataclass
class Consulted:
    id: str  # the task id the findings belong to, or the consult id
    member: str
    ask: str
    findings: str


@dataclass
class Tossed:
    task: Task
    to: str
    ask: str


@dataclass
class Assigned:
    task: Task
    member: str
    reason: str


@dataclass
class Applied:
    task: Task
    path: str


@dataclass
class ApplyPending:
    task: Task
    reason: str


@dataclass
class HumanDecision:
    task: Task
    accept: bool


@dataclass
class Question:
    task: Task
    member: str
    text: str


@dataclass
class HumanAnswer:
    task: Task
    answer: str


@dataclass
class Blocked:
    task: Task
    need: str


@dataclass
class Proposal:
    need: str
    names: list


@dataclass
class Approve:
    name: str


@dataclass
class Tick:
    pass


def on(event):
    def mark(f):
        f.ev = event
        return f
    return mark


class Bus:
    def __init__(s):
        s.subs = defaultdict(list)

    async def emit(s, e):
        await asyncio.gather(*(f(e) for f in s.subs[type(e)]))


class Node:
    def __init__(s, bus):
        s.bus = bus
        for n, f in inspect.getmembers(type(s), lambda m: hasattr(m, "ev")):
            bus.subs[f.ev].append(getattr(s, n))


class Ticker(Node):
    async def run(s, every=1800):
        while True:
            await s.bus.emit(Activity("daemon", "Running scheduled self-improvement checks (MCP discovery, repository scouting)."))
            try:
                await s.bus.emit(Tick())
            except Exception as e:
                log.exception("tick failed")
                await s.bus.emit(Activity("daemon", f"Maintenance check failed: {type(e).__name__}", "error"))
            await asyncio.sleep(every)


async def sh(*argv: str, cwd: str | None = None, input: str | None = None, timeout: float | None = None, env: dict | None = None) -> tuple[int, str]:
    rc, out, _ = await sh_split(*argv, cwd=cwd, input=input, timeout=timeout, env=env, merge=True)
    return rc, out


async def sh_split(*argv: str, cwd: str | None = None, input: str | None = None, timeout: float | None = None, env: dict | None = None, merge: bool = False) -> tuple[int, str, str]:
    """Run a command; return (rc, stdout, stderr). With merge, stderr is folded into stdout."""
    p = await asyncio.create_subprocess_exec(*argv, cwd=cwd, stdin=PIPE if input is not None else None, stdout=PIPE, stderr=STDOUT if merge else PIPE, env=env)
    try:
        out, err = await asyncio.wait_for(p.communicate(input.encode() if input is not None else None), timeout)
    except TimeoutError:
        p.kill()
        await p.wait()
        raise
    return p.returncode or 0, out.decode(errors="replace"), (err or b"").decode(errors="replace")


FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def jsonin(text):
    """The first JSON object in model output. Tolerates code fences, prose before or after, and
    several objects in a row (the old greedy regex joined them and raised "Extra data")."""
    text = str(text)
    blocks = [m.group(1) for m in FENCE.finditer(text)] + [text]
    dec = json.JSONDecoder()
    for block in blocks:
        for m in re.finditer(r"\{", block):
            try:
                obj, _ = dec.raw_decode(block, m.start())
            except ValueError:
                continue
            if isinstance(obj, dict):
                return obj
    raise ValueError(f"no JSON object in model output: {text[:80]}")
