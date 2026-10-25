import asyncio
import json
import time
from collections import Counter

import numpy as np

from agents import Agent
from core import Briefed, Bus, Node, Output, Task, on
from craft import Expert
from family import Judge, Lead, Panel, Suite
from mind import Brain

POOLS = {
    "docs": ["write", "docs", "readme", "explain", "guide", "tutorial", "document", "usage", "comment"],
    "bugfix": ["fix", "crash", "bug", "traceback", "error", "exception", "debug", "failing", "regression"],
    "refactor": ["refactor", "rename", "extract", "module", "class", "cleanup", "restructure", "method"],
    "algo": ["implement", "scheduler", "optimizer", "algorithm", "complexity", "heap", "graph", "dynamic"],
}
SKILL = {"haiku": (0.10, [0.95, 0.60, 0.30, 0.05]), "sonnet": (2.0, [0.98, 0.90, 0.75, 0.35]), "opus": (4.0, [0.99, 0.97, 0.92, 0.80])}
SUFFIX = ("", "s", "ing", "ed")
DRAFT = {"goal": "Ship it.", "verify": ["Run pytest -q"], "unasked": ["Add a rollback plan"], "alternatives": ["Use Option B"], "rare": ["Zed 9 profiler"], "start": ["Read the spec"], "methods": ["Apply TDD in 3 steps"], "pitfalls": ["Skipping tests"], "sources": ["RFC 1234"], "fast": "Do the core first."}


class World:
    def __init__(s, seed):
        s.rng, s.truth, s.kinds, s.cost, s.calls, s.by = np.random.default_rng(seed), {}, {}, 0.0, Counter(), Counter()

    def bill(s, role, price, tin, tout):
        s.cost += (tin + 5 * tout) * price / 1e6
        s.by[role] += (tin + 5 * tout) * price / 1e6
        s.calls[role] += 1

    def text(s, kind):
        words = s.rng.choice(POOLS[kind], 4, replace=False)
        t = " ".join(w + SUFFIX[s.rng.integers(4)] for w in words) + f" ticket{s.rng.integers(10**6)}"
        s.kinds[t] = kind
        return t


class SimAgent(Agent):
    def __init__(s, world, tier):
        s.world, s.tier, s.price = world, tier, SKILL[tier][0]

    async def ask(s, text, cwd=".", mcp=None):
        s.world.bill("work", s.price, 8000, 1500)
        kind = s.world.kinds[text.split("\n")[0]]
        ok = bool(s.world.rng.random() < SKILL[s.tier][1][list(POOLS).index(kind)])
        s.world.truth[cwd.rsplit("/")[-1]] = ok
        return "ok" if ok else "fail"


class SimPanelist(SimAgent):
    async def ask(s, text, cwd=".", mcp=None):
        s.world.bill("panel", s.price, 3000, 100)
        mu = 0.85 if s.world.truth[cwd.rsplit("/")[-1]] else 0.25
        return json.dumps({"accept": float(np.clip(s.world.rng.normal(mu, 0.1), 0, 1))})


class SimBrief(SimAgent):
    async def ask(s, text, cwd=".", mcp=None):
        s.world.bill("brief", s.price, 2000, 700)
        if text.startswith("Audit") or "Audit this expert brief" in text:
            return json.dumps({"methods": [0.9], "rare": [0.9]})
        return json.dumps({**DRAFT, "role": s.world.kinds[text.rsplit("Task: ", 1)[1]]})


class SimLead(Lead):
    async def workspace(s, t):
        return f"sim/{t.id}"

    async def discard(s, t):
        return None


class SimSuite(Suite):
    def __init__(s, bus, world):
        super().__init__(bus)
        s.world = world

    async def rc(s, o):
        return 0 if s.world.truth[o.task.cwd.rsplit("/")[-1]] else 1


class Fixed(Brain):
    def __init__(s, options, tier):
        super().__init__(options)
        s.tier = tier

    def pick(s, x):
        return s.tier


async def episode(policy, n=900, seed=7, lam=0.75, tries=3, tau=0.6, expert=True):
    world, bus, runs, briefs = World(seed), Bus(), {}, []
    members = {t: SimAgent(world, t) for t in SKILL}
    brain = Fixed(list(SKILL), policy) if policy else Brain(list(SKILL))
    brain.rng = np.random.default_rng(seed)
    ex = Expert(bus, SimBrief(world, "sonnet"), SimBrief(world, "haiku"), tau=tau) if expert else None
    SimLead(bus, members, brain, "sim", expert=ex, lam=lam, tries=tries)
    SimSuite(bus, world)
    Panel(bus, "p1", SimPanelist(world, "haiku"))
    Panel(bus, "p2", SimPanelist(world, "haiku"))
    Judge(bus, 3)

    class Tap(Node):
        @on(Output)
        async def out(s, o):
            runs.setdefault(o.task.id.rstrip("+"), []).append((o.member, o.task.id))

        @on(Briefed)
        async def brief(s, b):
            briefs.append((b.cached, b.brief["role"] == world.kinds[b.task.text]))

    Tap(bus)
    kinds, spent, t0 = list(POOLS), [0.0], time.perf_counter()
    for _ in range(n):
        await bus.emit(Task(world.text(kinds[world.rng.integers(len(kinds))])))
        spent.append(world.cost)
    wall = time.perf_counter() - t0
    done = list(runs.values())
    final = np.array([world.truth[r[-1][1]] for r in done])
    first = np.array([world.truth[r[0][1]] for r in done])
    w = n // 3
    hits = [ok for c, ok in briefs if c]
    return {
        "first_try": float(first[-w:].mean()),
        "final": float(final[-w:].mean()),
        "cost_task": (spent[n] - spent[n - w]) / w,
        "cost": world.cost,
        "mix": dict(Counter(r[0][0] for r in done[-w:])),
        "retries": sum(len(r) - 1 for r in done),
        "hit_rate": len(hits) / max(len(briefs), 1),
        "wrong_hits": 1 - (sum(hits) / len(hits) if hits else 1),
        "brief_share": world.by["brief"] / world.cost,
        "ms_task": wall / n * 1000,
        "n": n,
    }


def report(n=900):
    rows = [
        ("always opus", dict(policy="opus", tries=1)),
        ("always sonnet", dict(policy="sonnet", tries=1)),
        ("haiku, escalate on red", dict(policy="haiku")),
        ("learned router, no cascade", dict(lam=0.25, tries=1)),
        ("learned router + cascade", dict()),
    ]
    for name, kw in rows:
        r = asyncio.run(episode(kw.pop("policy", None), n, **kw))
        print(f"{name:25} final={r['final']:.0%} first_try={r['first_try']:.0%} ${r['cost_task']:.3f}/task retries={r['retries']} mix={r['mix']}")
    r = asyncio.run(episode(None, n))
    print(f"expert brief cache: hit_rate={r['hit_rate']:.0%} wrong_domain_hits={r['wrong_hits']:.1%} brief_share_of_cost={r['brief_share']:.0%}; harness overhead {r['ms_task']:.1f} ms/task")


if __name__ == "__main__":
    report()
