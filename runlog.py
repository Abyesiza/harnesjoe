"""Claude Code hook: records each Bash call for the dashboard. Never blocks anything.

Invoked as `runlog.py pre|post`; FAM_RUN_LOG is a JSONL file the daemon tails.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

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
    if event.get("tool_name") != "Bash":
        return 0
    command = str((event.get("tool_input") or {}).get("command", ""))
    cwd = str(event.get("cwd") or os.getcwd())
    use = str(event.get("tool_use_id") or "")
    if stage == "pre":
        _log({"stage": "pre", "use": use, "command": command[:500], "cwd": cwd, "ts": time.time()})
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


def run(stage: str, raw: str) -> int:
    try:
        return main(stage, raw)
    except Exception:  # noqa: BLE001 - logging must never stop a tool call
        return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else "pre", sys.stdin.read()))
