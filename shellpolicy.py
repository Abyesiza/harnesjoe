"""Shell policy for family agents: which commands a CLI may run, and where.

Two layers: native CLI permission flags (claude --allowedTools/--disallowedTools, copilot
--allow-tool/--deny-tool) and, for Claude, a PreToolUse hook (famguard.py) that runs `check`
on every Bash call before it executes. Configure with:
  FAM_SHELL_MODE   auto (default) | readonly | work | off
  FAM_SHELL_ALLOW  extra comma-separated command prefixes allowed in read-only jobs
  FAM_SHELL_DENY   extra comma-separated command prefixes always refused
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

# Hard-blocked for reads and writes alike: secrets only. Everything else may be read (and is reported).
HOME = Path.home()
PROTECTED_HOME = (".ssh", ".gnupg", ".aws", ".kube", ".password-store", ".docker", ".config/gh", ".netrc", ".git-credentials", ".fam/token", ".fam/state.sqlite3")
PROTECTED_SYSTEM = ("/etc/shadow", "/etc/gshadow", "/etc/sudoers", "/etc/sudoers.d", "/root", "/proc/self/environ")
READ = (
    "ls", "cd", "pwd", "cat", "head", "tail", "tree", "du", "wc", "stat", "file", "find", "grep", "rg", "echo",
    "sort", "uniq", "diff", "less", "realpath", "basename", "dirname", "which", "env", "date", "true",
    "git status", "git log", "git branch", "git diff", "git remote", "git show", "git rev-parse", "git ls-files", "git blame", "git -C",
    "python --version", "python3 --version", "node --version", "npm --version", "go version", "cargo --version",
    "pytest", "python -m pytest", "python3 -m pytest", ".venv/bin/python -m pytest", "mypy", "ruff check", "eslint", "tsc --noEmit",
    "npm test", "npm run test", "npm run lint", "go test", "go vet", "cargo test", "cargo check", "make test",
)
# Prefixes refused in every mode (also passed to the CLIs as native deny rules).
DENY = (
    "sudo", "su", "doas", "pkexec", "shutdown", "reboot", "mkfs", "dd", "chattr", "crontab", "systemctl",
    "git push --force", "git push -f", "git push --force-with-lease", "git push", "git clean -fdx",
    "rm -rf /", "rm -rf ~", "rm -rf *",
)
SECRET = re.compile(r"(^|/)(\.env(\.[\w.-]+)?|id_(rsa|dsa|ecdsa|ed25519)\w*|.*\.pem|.*\.key|credentials(\.json)?|\.netrc|\.pgpass)$")
SECRET_OK = re.compile(r"\.env\.(example|sample|template)$")
SPLIT = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")
SHELLS = {"sh", "bash", "zsh", "dash", "python", "python3", "node", "perl", "ruby"}
FETCH = {"curl", "wget"}


RUNS_CODE = ("pytest", "python -m pytest", "python3 -m pytest", ".venv/bin/python -m pytest", "npm", "go test", "go vet", "cargo", "make", "mypy", "eslint", "tsc", "ruff")


def mode(readonly: bool, untrusted: bool = False) -> str:
    """The shell mode for a job: off, inspect (untrusted code), readonly, or work."""
    env = os.environ.get("FAM_SHELL_MODE", "auto").strip().lower()
    if env == "off":
        return "off"
    if untrusted:
        return "inspect"
    if env == "readonly" or readonly:
        return "readonly"
    return "work"


def _extra(name: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in os.environ.get(name, "").split(",") if x.strip())


def allow_list() -> tuple[str, ...]:
    return (*READ, *_extra("FAM_SHELL_ALLOW"))


def deny_list() -> tuple[str, ...]:
    return (*DENY, *_extra("FAM_SHELL_DENY"))


def _inside(path: Path, dirs: list[Path]) -> bool:
    return any(path == d or d in path.parents for d in dirs)


def _starts(words: list[str], prefix: str) -> bool:
    p = prefix.split()
    return words[: len(p)] == p


def _branch(cwd: str) -> str:
    try:
        return subprocess.run(["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def protected(path: Path) -> str:
    """Why a resolved path is off-limits for any access, or '' when it may be read."""
    home = Path(os.environ.get("HOME", str(HOME))).resolve()
    for rel in PROTECTED_HOME:
        root = home / rel
        if path == root or root in path.parents or (rel.endswith("state.sqlite3") and path.parent == root.parent and path.name.startswith("state.sqlite3")):
            return f"{path} is a protected location"
    for raw in PROTECTED_SYSTEM:
        root = Path(raw)
        if path == root or root in path.parents:
            return f"{path} is a protected system file"
    if SECRET.search(path.name) and not SECRET_OK.search(path.name):
        return f"{path.name} may hold secrets; do not read or print it"
    return ""


def is_read(words: list[str], seg: str) -> bool:
    """An inspection command that cannot write: may look anywhere except protected paths."""
    if not any(_starts(words, a) for a in allow_list()) or any(_starts(words, r) for r in RUNS_CODE):
        return False
    if words[0] == "find" and {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint"} & set(words):
        return False
    if re.search(r"(?<![0-9&])>{1,2}(?!&)|\btee\b", seg) and "/dev/null" not in seg:
        return False
    if words[0] == "git" and len(words) > 1 and words[1] == "branch" and any(w in {"-d", "-D", "-m", "-M", "--delete"} for w in words):
        return False
    return words[0] not in FETCH


def evaluate(command: str, cwd: str, dirs: list[str], job_mode: str) -> tuple[bool, str, list[str]]:
    """(allowed, reason, paths read outside the approved folders). Paths are resolved, so `..` and
    symlinks cannot reach a protected place. Reads may go anywhere except protected paths and are
    reported; writes, builds and anything that runs code stay inside the approved folders."""
    if job_mode == "off":
        return False, "shell commands are turned off (FAM_SHELL_MODE=off)", []
    roots = [Path(d).expanduser().resolve() for d in dirs if d]
    here = Path(cwd).expanduser().resolve()
    outside: list[str] = []
    if why := protected(here):
        return False, why, []
    if re.search(r"\$\(|`", command):
        return False, "command substitution is not allowed", []
    if re.search(r"\|\s*(sudo\s+)?(sh|bash|zsh|dash|python3?|node|perl|ruby)\b", command) and re.search(r"\b(curl|wget)\b", command):
        return False, "piping a download into a shell is not allowed", []
    segments = [x for x in SPLIT.split(command.strip()) if x]
    here_inside = bool(roots) and _inside(here, roots)
    for seg in segments:
        try:
            words = shlex.split(seg)
        except ValueError:
            return False, "could not parse the command", []
        while words and re.fullmatch(r"\w+=.*", words[0]):  # leading VAR=value
            words = words[1:]
        if not words:
            continue
        for bad in deny_list():
            if _starts(words, bad):
                return False, f"`{bad}` is blocked", []
        if words[0] == "git" and words[1:3] == ["reset", "--hard"] and _branch(str(here)) in {"main", "master", "develop"}:
            return False, "git reset --hard on a main branch is blocked", []
        if words[0] in SHELLS and any(w in {"-c", "-e"} for w in words[1:2]) and job_mode in ("readonly", "inspect"):
            return False, "inline scripts are not allowed in read-only jobs", []
        read = is_read(words, seg)
        if job_mode == "inspect" and any(_starts(words, r) for r in RUNS_CODE):
            return False, "untrusted code is inspected only, never executed", []
        if job_mode in ("readonly", "inspect") and not read:
            if not any(_starts(words, a) for a in allow_list()):
                return False, f"`{words[0]}` is not on the read-only command list", []
            if words[0] in FETCH:
                return False, "downloads are not allowed in read-only jobs", []
            if not any(_starts(words, r) for r in RUNS_CODE):
                return False, "writing files is not allowed in read-only jobs", []
        if not here_inside and not read and words[0] not in ("cd", "pwd", "true"):
            return False, f"working folder {here} is not inside an approved folder (only reads are allowed there)", []
        if not here_inside and words[0] not in ("cd", "pwd", "true"):
            outside.append(str(here))
        for w in words[1:] + ([words[0]] if "/" in words[0] and not words[0].startswith(("/usr/", "/bin/")) else []):
            arg = w.split("=", 1)[1] if w.startswith("-") and "=" in w else w
            if arg.startswith("-") or not arg or arg == "/dev/null":
                continue
            if not (arg.startswith(("/", "~", ".")) or "/" in arg or SECRET.search(arg)):
                continue
            path = (here / Path(arg).expanduser()).resolve()
            if why := protected(path):
                return False, why, []
            if not (roots and _inside(path, roots)):
                if not read:
                    return False, f"{arg} is outside the approved folders (writes and runs stay inside them)", []
                outside.append(str(path))
    return True, "", list(dict.fromkeys(outside))


def check(command: str, cwd: str, dirs: list[str], job_mode: str) -> tuple[bool, str]:
    ok, why, _ = evaluate(command, cwd, dirs, job_mode)
    return ok, why


def check_path(tool: str, path: str, dirs: list[str], job_mode: str) -> tuple[bool, str, bool]:
    """Vet a file tool (Read/Glob/Grep/Edit/Write). Returns (allowed, reason, outside_approved)."""
    p = Path(path).expanduser().resolve()
    if why := protected(p):
        return False, why, False
    roots = [Path(d).expanduser().resolve() for d in dirs if d]
    inside = bool(roots) and _inside(p, roots)
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        if job_mode != "work":
            return False, "editing is not allowed in read-only jobs", False
        if not inside:
            return False, f"{p} is outside the approved folders (edits stay inside them)", False
    return True, "", not inside


def deny_rules() -> list[str]:
    """Claude deny rules. A rule with its own `*` must not also take the `:*` prefix suffix
    (Claude warns "mixes * with the trailing :* prefix syntax" and treats it literally)."""
    return [f"Bash({d})" if "*" in d else f"Bash({d}:*)" for d in deny_list()]


def claude_flags(job_mode: str, settings: str) -> list[str]:
    """Native Claude permission flags plus the guard hook settings."""
    if job_mode == "off":
        return ["--disallowedTools", "Bash", "Edit", "Write", "NotebookEdit"]
    deny = deny_rules()
    if job_mode in ("readonly", "inspect"):
        cmds = [c for c in allow_list() if job_mode == "readonly" or not any(c.startswith(r) for r in RUNS_CODE)]
        allow = ["Read", "Glob", "Grep", "LS", *(f"Bash({c}:*)" for c in cmds)]
        return ["--settings", settings, "--allowedTools", *allow, "--disallowedTools", "Edit", "Write", "NotebookEdit", *deny]
    return ["--settings", settings, "--permission-mode", "acceptEdits", "--allowedTools", "Bash", "Read", "Glob", "Grep", "LS", "Edit", "Write", "--disallowedTools", *deny]


def copilot_flags(job_mode: str) -> list[str]:
    """Native Copilot permission flags. Deny rules always win over allow rules."""
    deny = [a for d in deny_list() if not d.startswith("rm ") for a in ("--deny-tool", f"shell({d})")]
    secrets = ["--deny-tool", "write(.env)"]
    if job_mode == "off":
        return ["--deny-tool", "shell", "--deny-tool", "write"]
    if job_mode in ("readonly", "inspect"):
        cmds = [c for c in allow_list() if job_mode == "readonly" or not any(c.startswith(r) for r in RUNS_CODE)]
        return [*(a for c in cmds for a in ("--allow-tool", f"shell({c}:*)" if " " in c else f"shell({c})")), "--deny-tool", "write", *deny]
    return ["--allow-all-tools", *deny, *secrets]
