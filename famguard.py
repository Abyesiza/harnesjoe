"""Claude Code hook: vets each Bash call against shellpolicy and logs it for the dashboard.

Invoked as `famguard.py pre|post`; context comes from FAM_GUARD_DIRS (os.pathsep-joined),
FAM_GUARD_MODE and FAM_RUN_LOG (a JSONL file the daemon tails).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shellpolicy import check_path, evaluate  # noqa: E402

FILE_TOOLS = {"Read": "file_path", "Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path", "Glob": "path", "Grep": "path", "LS": "path"}

OUT = 2000
# Tokens that must never reach the run log or the dashboard (provider keys, GitHub/AWS/Slack tokens, bearer headers, key=value secrets).
REDACT = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|xox[abprs]-[A-Za-z0-9-]{10,}|tvly-[A-Za-z0-9]{16,})"
    r"|((?:bearer|authorization:)\s+)[A-Za-z0-9._~+/=-]{12,}"
    r"|(\b[\w-]*(?:api[_-]?key|token|secret|password|passwd)[\w-]*\s*[=:]\s*)[^\s'\"&]{6,}",
    re.IGNORECASE,
)


def redact(text: str) -> str:
    return REDACT.sub(lambda m: (m.group(2) or m.group(3) or "") + "[redacted]", text)


def _log(entry: dict) -> None:
    for key in ("command", "output"):
        if isinstance(entry.get(key), str):
            entry[key] = redact(entry[key])
    path = os.environ.get("FAM_RUN_LOG")
    if path:
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")


def main(stage: str, raw: str) -> int:
    event = json.loads(raw or "{}")
    tool = event.get("tool_name")
    if tool in FILE_TOOLS:
        return file_tool(stage, event, str(tool))
    if tool != "Bash":
        return 0
    command = str((event.get("tool_input") or {}).get("command", ""))
    cwd = str(event.get("cwd") or os.getcwd())
    use = str(event.get("tool_use_id") or "")
    if stage == "pre":
        dirs = [d for d in os.environ.get("FAM_GUARD_DIRS", "").split(os.pathsep) if d]
        ok, why, outside = evaluate(command, cwd, dirs, os.environ.get("FAM_GUARD_MODE", "readonly"))
        _log({"stage": "pre", "use": use, "command": command[:500], "cwd": cwd, "ts": time.time(), "allowed": ok, "reason": why, "outside": outside})
        if not ok:
            print(f"Blocked by Fam shell policy: {why}", file=sys.stderr)
            return 2
        return 0
    resp = event.get("tool_response") or {}
    if not isinstance(resp, dict):
        resp = {"stdout": str(resp)}
    out = (str(resp.get("stdout", "")) + ("\n" + str(resp["stderr"]) if resp.get("stderr") else "")).strip()
    code = resp.get("exit_code", resp.get("exitCode", resp.get("returnCode")))
    if code is None:
        code = 1 if resp.get("interrupted") or resp.get("is_error") or stage == "fail" else 0
    _log({"stage": "post", "use": use, "command": command[:500], "cwd": cwd, "ts": time.time(), "exit": code, "output": out[-OUT:]})
    return 0


def file_tool(stage: str, event: dict, tool: str) -> int:
    """Read/Glob/Grep may look anywhere but secrets (reported when outside the approved folders);
    Edit/Write stay inside them. Only reads outside and refusals are logged, to keep the log short."""
    if stage != "pre":
        return 0
    cwd = str(event.get("cwd") or os.getcwd())
    raw = str((event.get("tool_input") or {}).get(FILE_TOOLS[tool]) or cwd)
    path = raw if os.path.isabs(os.path.expanduser(raw)) else os.path.join(cwd, raw)
    dirs = [d for d in os.environ.get("FAM_GUARD_DIRS", "").split(os.pathsep) if d]
    ok, why, outside = check_path(tool, path, dirs, os.environ.get("FAM_GUARD_MODE", "readonly"))
    if not ok or outside:
        _log({"stage": "pre", "use": str(event.get("tool_use_id") or ""), "command": f"{tool} {raw}"[:500], "cwd": cwd, "ts": time.time(),
              "allowed": ok, "reason": why, "outside": [os.path.realpath(path)] if ok else [], "tool": tool})
    if not ok:
        print(f"Blocked by Fam shell policy: {why}", file=sys.stderr)
        return 2
    return 0


def run(stage: str, raw: str) -> int:
    """Fail closed before a tool runs: a crash (exit 1) would let Claude Code run the call anyway."""
    try:
        return main(stage, raw)
    except Exception as e:  # noqa: BLE001 - any guard failure must block, not allow
        if stage != "pre":
            return 0
        print(f"Blocked by Fam shell policy: the guard could not check this call ({type(e).__name__})", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else "pre", sys.stdin.read()))
