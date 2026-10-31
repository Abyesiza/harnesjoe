import json
import re

from core import Briefed, Node
from mind import Memo, enc

FIELDS = {
    "role": "the professional who does this best",
    "goal": "the real outcome behind the request",
    "verify": "how an expert checks the result is right",
    "unasked": "needed but not requested",
    "alternatives": "other credible routes and when they win",
    "rare": "uncommon tools, datasets or materials insiders use",
    "start": "their first three moves, in order",
    "methods": "named techniques and standards",
    "pitfalls": "what amateurs miss",
    "sources": "references that back the methods",
    "fast": "the 20 percent of work that gives 80 percent of the value",
    "say": "one short line to the user, in your voice",
}
AXES = {
    "altitude": ("role", "goal", "verify"),
    "range": ("unasked", "alternatives", "rare"),
    "depth": ("start", "methods", "pitfalls", "sources"),
    "speed": ("fast",),
    "caution": ("unverified",),
}
BRIEF = (
    "You advise how a world-class professional would do this task. Be concrete: name techniques, tools, standards "
    "and numbers, never generalities. Reply only JSON with keys: "
    + "; ".join(f"{k} ({d})" for k, d in FIELDS.items())
    + ". role, goal, fast and say are one sentence; every other value is a list of short strings."
)
CHECK = (
    'Audit this expert brief. Rate 0..1 how sure you are that each item in "methods" and "rare" is real and applicable. '
    'Reply only {"methods": [<score per item>], "rare": [<score per item>]}.\n'
)


def vague(item):
    return not re.search(r"\d|[A-Z]{2,}|\w[A-Z]|\s[A-Z][a-z]|\w[-_/.]\w", item)


def render(b):
    rows = (
        f"[{a}] " + " | ".join(f"{k}: {'; '.join(v) if isinstance(v, list) else v}" for k in ks if (v := b.get(k)))
        for a, ks in AXES.items()
    )
    return "\n".join(r for r in rows if not r.endswith("] "))


class Expert(Node):
    def __init__(s, bus, drafter, auditor, path=None, floor=0.6, tau=0.6):
        super().__init__(bus)
        s.drafter, s.auditor, s.floor, s.tau, s.memo = drafter, auditor, floor, tau, Memo(path=path)

    async def draft(s, t, nudge=""):
        return await s.drafter.ask_json(f"{BRIEF}{nudge}\nTask: {t.text}", t.cwd)

    async def make(s, t):
        b = await s.draft(t)
        items = [*b["methods"], *b["rare"]]
        if sum(map(vague, items)) * 2 > len(items):
            b = await s.draft(t, " Your last draft was too generic: name specific techniques, tools, standards and numbers.")
        a = await s.auditor.ask_json(CHECK + json.dumps(b), t.cwd)
        b["unverified"] = []
        for k in ("methods", "rare"):
            ok = [c >= s.floor for c in (*a[k], *[0] * len(b[k]))]
            b["unverified"] += [i for i, o in zip(b[k], ok) if not o]
            b[k] = [i for i, o in zip(b[k], ok) if o]
        return b

    async def brief(s, t):
        x = enc(t.text)
        b = s.memo.get(x, s.tau)
        hit = b is not None
        if not hit:
            try:
                b = await s.make(t)
            except (KeyError, ValueError, TypeError):
                return {}
            s.memo.put(x, b)
        await s.bus.emit(Briefed(t, b, hit))
        return b
