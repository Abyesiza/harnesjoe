"""Plain-language reports the family posts back to Joel when work finishes."""

from __future__ import annotations

from typing import Any

MAX = 300


def _short(text: Any, limit: int = MAX) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _duration(seconds: float) -> str:
    seconds = max(0, round(seconds))
    return f"{seconds // 60}m {seconds % 60}s" if seconds >= 60 else f"{seconds}s"


def run_line(runs: list[dict]) -> str:
    """Summarise the CLI calls for one task: which CLIs ran, fallbacks taken, wall time."""
    if not runs:
        return "Ran: no CLI calls recorded."
    clis: dict[str, int] = {}
    for run in runs:
        clis[run["cli"]] = clis.get(run["cli"], 0) + 1
    used = ", ".join(f"{cli} ×{n}" for cli, n in clis.items())
    fallbacks = [f"{f.get('failed')}→{f.get('to')} ({_short(f.get('reason'), 80)})" for r in runs for f in r.get("fallbacks", []) if isinstance(f, dict)]
    failed = sum(1 for r in runs if r.get("status") not in ("done", "running"))
    ends = [r["ended"] for r in runs if r.get("ended")]
    took = _duration(max(ends) - min(r["started"] for r in runs)) if ends else "still running"
    cmds = [c for r in runs for c in r.get("commands", [])]
    line = f"Ran: {used} · fallbacks: {'; '.join(fallbacks) if fallbacks else 'none'} · took {took}"
    line += f" · {failed} failed call(s)" if failed else ""
    return line + commands_block([c for c in cmds if c.get("status") != "read"]) + permissions_block(cmds)


def permissions_block(cmds: list[dict]) -> str:
    """Which folders outside the approved ones were read (reads are allowed, but always reported)."""
    paths = list(dict.fromkeys(p for c in cmds for p in c.get("outside") or []))
    if not paths:
        return ""
    shown = ", ".join(_short(p, 90) for p in paths[:6]) + (f" …and {len(paths) - 6} more" if len(paths) > 6 else "")
    return f"\nPermissions: read outside approved folders: {shown}"


def commands_block(cmds: list[dict]) -> str:
    if not cmds:
        return ""
    def one(c: dict) -> str:
        state = "blocked: " + _short(c.get("output"), 80) if c.get("status") == "blocked" else f"exit {c.get('exit')}" + (f", {c['seconds']}s" if c.get("seconds") is not None else "")
        return f"\n  • {_short(c.get('command'), 100)} ({state})"
    shown = "".join(one(c) for c in cmds[:8])
    more = f"\n  • …and {len(cmds) - 8} more" if len(cmds) > 8 else ""
    return f"\nCommands run ({len(cmds)}):{shown}{more}"


def research(d: dict, runs: list[dict]) -> str:
    cands = d.get("candidates") or {}
    top = sorted(cands.items(), key=lambda kv: -float(kv[1] or 0))[:3]
    sources = d.get("sources") or []
    providers = sorted({s.get("provider", "") for s in sources if isinstance(s, dict) and s.get("provider")})
    how = {"settled": "settled early", "converged": "answers stopped changing", "budget": "round limit reached"}.get(d.get("reason", ""), d.get("reason", ""))
    lines = [
        f"Quill — research job {d.get('id', '')} finished ({d.get('rounds', 0)} round(s), {how}).",
        f"Asked: {_short(d.get('question'), 200)}",
        f"Result: {_short(d.get('say')) or 'Quill gave no summary.'}",
    ]
    if top:
        lines.append("Best answers: " + "; ".join(f"{_short(k, 120)} ({float(v or 0):.0%})" for k, v in top))
    if sources:
        lines.append(f"Sources ({len(sources)}, via {', '.join(providers) or 'web'}): " + ", ".join(_short(s.get("url", s), 90) for s in sources[:5] if isinstance(s, dict)))
    else:
        lines.append("Sources: none from the web (local folders or model knowledge only).")
    lines.append(run_line(runs))
    problems = []
    if not top:
        problems.append("no answer reached")
    if d.get("reason") == "budget" and top and float(top[0][1] or 0) < 0.7:
        problems.append("low confidence at the round limit")
    lines.append("Problems: " + ("; ".join(problems) if problems else "none"))
    lines.append("Next: ask “research …” again with more detail, or ask Quill a follow-up." if problems else "Next: nothing needed unless you want a deeper look.")
    return "\n".join(lines)


def decision(d: dict, runs: list[dict], first: str) -> str:
    task = d.get("task") or {}
    outcome = "needs your decision" if d.get("escalate") else "accepted" if d.get("accept") else "rejected"
    nxt = (
        f"Next: send “accept task {task.get('id')}” or “reject task {task.get('id')}”."
        if d.get("escalate")
        else "Next: the change is applied locally (nothing pushed)." if d.get("accept") else "Next: Skipper will retry or ask you."
    )
    return "\n".join([
        first,
        f"Asked: {_short(task.get('text'), 200)}",
        f"Done by: {d.get('member', '?')} · review {outcome} (reviewer spread {float(d.get('spread') or 0):.2f})",
        f"Where: {task.get('cwd', '.')}",
        run_line(runs),
        nxt,
    ])


def error(d: dict, runs: list[dict], first: str) -> str:
    return "\n".join([first, f"Reason: {_short(d.get('message'), 300)}", run_line(runs), f"Next: fix the reason above, then send “resume task {d.get('task', '')}”."])


def scouted(d: dict) -> str:
    return "\n".join([
        f"Ranger — reviewed {d.get('repo', '')} (score {float(d.get('score') or 0):.2f}).",
        f"Result: {_short(d.get('say')) or 'No summary.'}",
        f"Found: {len(d.get('skills') or [])} skill(s), {len(d.get('lessons') or [])} lesson(s) saved locally.",
        "Next: say “approve <name>” for any proposal you want.",
    ])
