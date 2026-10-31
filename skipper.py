import asyncio
import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import asdict, is_dataclass
from pathlib import Path

import skills
from core import ACCESS, Access, Approve, Consulted, Correction, Focus, HumanAnswer, ResearchRequest, Task
from link import ready

INTENTS = {"task", "status", "member_chat", "answer_question", "research", "focus", "teach", "approve_tool", "answer", "consult", "note"}
LIMITS = {"intent": 40, "task": 8000, "member": 120, "reply": 4000, "name": 200}
SYSTEM = {"/", "/bin", "/boot", "/dev", "/etc", "/lib", "/lib32", "/lib64", "/opt", "/proc", "/root", "/run", "/sbin", "/srv", "/sys", "/tmp", "/usr", "/var", "/home", "/snap", "/mnt", "/media"}
SENSITIVE = {".ssh", ".gnupg", ".aws", ".kube", ".password-store", ".fam"}
WINDOWS = [Path(os.environ[v]) for v in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData") if os.environ.get(v)]
ROOT = r"(?:~[\\/]?|/|[A-Za-z]:[\\/]|\\\\)"  # ~/x, /x, C:\x or C:/x, \\server\share
QUOTED = re.compile(rf"[\"'`“”‘’]({ROOT}[^\"'`“”‘’\n]+)[\"'`“”‘’]")
PATH_START = re.compile(rf"(?<![\w/\\]){ROOT}")
TRAILING = ".,:;!?)]}"
WRITE_ASK = re.compile(r"\b(edit|write|change|fix|create|add|delete|remove|rename|move|update|modify|refactor|implement|save|replace|clean ?up)\b", re.I)


def clean_plan(plan: object) -> dict[str, str]:
    """Model output is untrusted: keep only known string fields, bounded, with an allowed intent."""
    if not isinstance(plan, dict):
        raise ValueError("Skipper returned an unreadable plan. Please rephrase the request.")
    out = {k: str(plan.get(k) or "").strip()[:n] for k, n in LIMITS.items()}
    out["member"] = out["member"].lower()
    if out["intent"] not in INTENTS:
        raise ValueError("Skipper could not determine a safe next step. Please rephrase the request.")
    return out


def safe_folder(raw: str) -> Path:
    """Resolve a user-named folder and refuse system or credential locations."""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError("Use an absolute folder path, such as C:\\Users\\you\\Projects\\app or /home/you/Projects/app.")
    path = path.resolve()
    if path.is_file():
        path = path.parent
    if not path.is_dir():
        raise ValueError(f"{path} is not an accessible directory.")
    if str(path) in SYSTEM or path == Path.home().resolve() or path == Path(path.anchor):
        raise ValueError(f"{path} is too broad or a system folder; choose a project folder inside it.")
    work = Path(os.environ.get("FAM_HOME", Path.home() / ".fam")).expanduser().resolve() / "work"  # task worktrees hold no secrets
    if work in path.parents:
        return path
    if (
        any(part in SENSITIVE for part in path.parts)
        or any(str(path).startswith(s + "/") for s in SYSTEM - {"/", "/home", "/tmp", "/mnt", "/media", "/srv", "/opt"})
        or any(path == w or w in path.parents for w in WINDOWS)
    ):
        raise ValueError(f"{path} is a protected location.")
    return path


RESEARCH_FOLLOWUP = re.compile(
    r"\b(what did (quill|the research|research) (find|say|conclude)"
    r"|what (has|have) (quill|the research) found"
    r"|(last|latest|previous|recent) (research )?(result|finding|job|report)s?"
    r"|quill(?:'s|’s)? (status|progress|findings?|results?)"
    r"|research (status|progress|results?|findings?)"
    r"|is (quill|the research) (done|finished|still)"
    r"|how far (is|has) quill"
    r"|what (did |has )?quill (found|find))\b"
)
NEW_RESEARCH = re.compile(
    r"\b(research (this|into|on|about|how|what|whether|why|the)|look (through|into|at)|investigate|survey|list|compare|review|check out)\b"
)


NEW_RESEARCH_ASK = re.compile(r"^\s*(?:ask|tell|have|get)\s+quill\s+(?:to\s+)?(?:research|investigate|survey|look\s+(?:into|through|at))\b", re.I)


def is_research_followup(text: str) -> bool:
    """Only explicit questions about earlier research replay its result; new requests start a new job."""
    lower = text.lower()
    return bool(RESEARCH_FOLLOWUP.search(lower)) and not NEW_RESEARCH.search(lower) and not folders_in(text)


def _as_dir(raw: str) -> str | None:
    """The folder a raw path names; a file counts as its folder, so “fix C:\\app\\main.py” works in C:\\app."""
    candidate = Path(raw.strip().rstrip(TRAILING)).expanduser()
    if not candidate.is_absolute():
        return None
    if candidate.is_file():
        candidate = candidate.parent
    return str(candidate) if candidate.is_dir() else None


def folders_in(text: str) -> list[str]:
    """Every existing directory named in a message, including quoted or unquoted paths with spaces."""
    found: list[str] = []
    for raw in QUOTED.findall(text):
        if (d := _as_dir(raw)) and d not in found:
            found.append(d)
    rest = QUOTED.sub(" ", text)
    for start in PATH_START.finditer(rest):
        line = rest[start.start():].split("\n", 1)[0]
        words = line.split(" ")
        # Longest run of words that is a real directory wins, so "/a/Metis Projects, read-only" -> "/a/Metis Projects".
        for n in range(len(words), 0, -1):
            if d := _as_dir(" ".join(words[:n])):
                if d not in found:
                    found.append(d)
                break
    return found


async def read_only(agent, prompt: str, cwd: str, folders: list[str], write: bool = False) -> str:
    """Ask a member something with access to the folders the user named: read-only unless the user asked for changes."""
    token = ACCESS.set(Access(tuple(folders), readonly=not write))
    try:
        return await agent.ask(prompt, folders[0] if folders else cwd)
    finally:
        ACCESS.reset(token)


def lobby() -> str:
    """Neutral working folder for member chats with no named folder. Never ~/.fam: that holds the
    token, the state database and logs, and is a git repo the CLIs would otherwise wander into."""
    path = Path.home() / ".local" / "share" / "fam" / "lobby"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return str(path)


JOB_ID = re.compile(r"\b([0-9a-f]{8})\b")
MOVE = re.compile(r"^\s*(?:please\s+)?(?:cd|go to|move to|switch to|change to|open|work in)\s+(.+?)\s*$", re.I)
CONSULT_ASK = re.compile(r"\b(verif\w*|test\w*|review|validate|audit|confirm|double-check)\b", re.I)
FOUND = re.compile(r"\bwhat (?:did |has |have )?(\w+) (?:find|found|say|said|conclude|concluded|report|reported)\b", re.I)
MODEL_Q = re.compile(r"\b(?:which|what) (?:model|llm|cli)s?\b.*?\b(?:does|do|is|for|of)\s+(\w+)|\b(\w+)(?:'s|’s) model\b", re.I)
LOCAL_HINT = re.compile(r"\b(project|folder|foldr|repo|repository|codebase|directory|dir)\b", re.I)
SEARCH_ROOTS = ("Desktop", "Desktop/my_projects", "Documents", "Downloads", "Projects", "projects", "code", "src", "dev", "work", "repos", "source/repos", "")
STOP = {"the", "a", "an", "my", "our", "this", "that", "project", "folder", "foldr", "repo", "codebase", "read", "through", "lets", "let", "do", "look", "into", "at", "of", "in", "on", "for", "and"}


def local_projects(text: str, approved: list[str]) -> list[str]:
    """Folders on this machine whose name matches a word in the message: approved folders first,
    then common project locations. Local projects win over a web search for the same name."""
    words = {w for w in re.findall(r"[a-z0-9][a-z0-9_.-]{2,}", text.lower()) if w not in STOP}
    if not words:
        return []
    def hit(name: str) -> bool:
        n = name.lower()
        return any(n == w or n.startswith(w) or (len(n) >= 4 and n in w) for w in words)
    found = [d for d in approved if hit(Path(d).name)]
    if found:
        return found
    home = Path.home()
    # OneDrive moves Desktop and Documents under ~/OneDrive*/ on Windows.
    roots = [*SEARCH_ROOTS, *(f"{d.name}/{r}" for d in home.glob("OneDrive*") if d.is_dir() for r in ("Desktop", "Documents", ""))]
    for root in roots:
        base = home / root
        try:
            kids = sorted(base.iterdir()) if base.is_dir() else []
        except OSError:
            continue
        for kid in kids:
            if kid.is_dir() and not kid.name.startswith(".") and hit(kid.name) and str(kid) not in found:
                found.append(str(kid))
    # Prefer real projects (git repos) and exact names.
    return sorted(found, key=lambda d: (not (Path(d) / ".git").exists(), Path(d).name.lower() not in words))[:3]


PLAN = (
    "You are Skipper, the user's conversational chief of staff for an autonomous software family. "
    "Classify the request and decide the next safe step. Only assign implementation work after the user asks for a concrete software outcome. "
    "Questions should be answered directly. Do not invent that work, tests, or approvals happened. "
    "Choose only one intent: task, status, member_chat, answer_question, research, focus, teach, approve_tool, answer, consult, or note. "
    "Use consult when a task needs help, verification, testing or review from any family members: put their keys comma-separated in member, what to check in task, and the task id in name when known. They work in the background and Skipper reports their findings. "
    "Every task listed under a member's running list is active at the same time; a new message never pauses or replaces them, and you can answer about any of them. "
    "When the user asks how a specific task or piece of work is going, use intent answer and report from the task list (id, what it is, who has it, its state); say plainly if nothing matches. Use status only for a general overview. "
    "Use note when the user adds information, a correction or a hint for a task that is already running; put the note in task and the task id in name. "
    "Use teach when the user states how they like code written or work done (a preference, a correction, a standing rule); put it in task as one imperative sentence. "
    "For tasks choose a builder by capability and current active workload, or leave member empty for automatic routing. "
    "For member_chat choose any family member by key. For approve_tool provide the exact existing proposal name. "
    "When a pending family question exists, use answer_question only if the user's message answers it; put the answer in task. "
    'Reply only JSON: {"intent":"...","task":"...","member":"...","reply":"...","name":"..."}.\n'
)


class StateStore:
    def __init__(s, path):
        s.path = Path(path)
        s.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        s.path.parent.chmod(0o700)
        with s.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    type TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    task_id TEXT
                );
                CREATE TABLE IF NOT EXISTS folder_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    requested_at REAL NOT NULL,
                    approved_at REAL,
                    UNIQUE(path)
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    task TEXT NOT NULL DEFAULT '',
                    cli TEXT NOT NULL,
                    cwd TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started REAL NOT NULL,
                    ended REAL,
                    output TEXT NOT NULL DEFAULT '',
                    fallbacks TEXT NOT NULL DEFAULT '[]',
                    commands TEXT NOT NULL DEFAULT '[]'
                );
                """
            )
            cols = {r[1] for r in db.execute("PRAGMA table_info(runs)")}
            if "commands" not in cols:  # databases created before shell logging
                db.execute("ALTER TABLE runs ADD COLUMN commands TEXT NOT NULL DEFAULT '[]'")
        s.path.chmod(0o600)

    def connect(s):
        db = sqlite3.connect(s.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def event(s, kind, data):
        if is_dataclass(data):
            data = asdict(data)
        with s.connect() as db:
            cur = db.execute(
                "INSERT INTO events (ts, type, data) VALUES (?, ?, ?)",
                (time.time(), kind, json.dumps(data, default=str)),
            )
            return {"id": cur.lastrowid, "ts": time.time(), "type": kind, "data": data}

    def events(s, limit=1000):
        with s.connect() as db:
            rows = db.execute(
                "SELECT id, ts, type, data FROM events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"id": row["id"], "ts": row["ts"], "type": row["type"], "data": json.loads(row["data"])}
            for row in reversed(rows)
        ]

    def message(s, role, text, task_id=None):
        with s.connect() as db:
            cur = db.execute(
                "INSERT INTO messages (ts, role, text, task_id) VALUES (?, ?, ?, ?)",
                (time.time(), role, text, task_id),
            )
            return {"id": cur.lastrowid, "ts": time.time(), "role": role, "text": text, "task": task_id}

    def messages(s, limit=100):
        with s.connect() as db:
            rows = db.execute(
                "SELECT id, ts, role, text, task_id FROM messages ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"id": r["id"], "ts": r["ts"], "role": r["role"], "text": r["text"], "task": r["task_id"]}
            for r in reversed(rows)
        ]

    def pending_folders(s):
        with s.connect() as db:
            rows = db.execute(
                "SELECT id, path, requested_at FROM folder_requests WHERE approved_at IS NULL ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def approve_folder(s, request_id: int, path: str) -> None:
        with s.connect() as db:
            other = db.execute("SELECT id FROM folder_requests WHERE path=? AND id<>?", (path, request_id)).fetchone()
            if other:
                # The request resolved to a folder already on record; approve that row instead of colliding on UNIQUE(path).
                db.execute("DELETE FROM folder_requests WHERE id=?", (request_id,))
                db.execute("UPDATE folder_requests SET approved_at=COALESCE(approved_at, ?) WHERE id=?", (time.time(), other["id"]))
                return
            db.execute(
                "UPDATE folder_requests SET path=?, approved_at=? WHERE id=? AND approved_at IS NULL",
                (path, time.time(), request_id),
            )

    def grant_folder(s, path: str) -> bool:
        """Approve a resolved folder directly; returns True when it was not approved before."""
        with s.connect() as db:
            row = db.execute("SELECT approved_at FROM folder_requests WHERE path=?", (path,)).fetchone()
            db.execute(
                "INSERT INTO folder_requests (path, requested_at, approved_at) VALUES (?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET approved_at=COALESCE(folder_requests.approved_at, excluded.approved_at)",
                (path, time.time(), time.time()),
            )
        return not (row and row["approved_at"])

    def run_start(s, cli: str, cwd: str, prompt: str, task: str = "") -> dict:
        row = {"id": uuid.uuid4().hex[:10], "task": task, "cli": cli, "cwd": cwd, "prompt": " ".join(prompt.split())[:300], "status": "running", "started": time.time()}
        with s.connect() as db:
            db.execute(
                "INSERT INTO runs (id, task, cli, cwd, prompt, status, started) VALUES (:id, :task, :cli, :cwd, :prompt, :status, :started)",
                row,
            )
        return row

    def run_fallback(s, run_id: str, failed: str, to: str, reason: str) -> None:
        with s.connect() as db:
            got = db.execute("SELECT fallbacks FROM runs WHERE id=?", (run_id,)).fetchone()
            if not got:
                return
            chain = [*json.loads(got["fallbacks"]), {"failed": failed, "to": to, "reason": reason[:300]}]
            db.execute("UPDATE runs SET fallbacks=?, cli=? WHERE id=?", (json.dumps(chain), to, run_id))

    def run_command(s, run_id: str, entry: dict) -> list[dict]:
        """Insert or update one shell command (matched by command+started) on a run."""
        with s.connect() as db:
            got = db.execute("SELECT commands FROM runs WHERE id=?", (run_id,)).fetchone()
            if not got:
                return []
            cmds = json.loads(got["commands"] or "[]")
            key = (entry.get("command"), entry.get("started"))
            cmds = [c for c in cmds if (c.get("command"), c.get("started")) != key] + [entry]
            db.execute("UPDATE runs SET commands=? WHERE id=?", (json.dumps(cmds[-100:]), run_id))
        return cmds

    def run_finish(s, run_id: str, status: str, output: str, cli: str = "") -> dict | None:
        with s.connect() as db:
            db.execute(
                "UPDATE runs SET status=?, ended=?, output=?, cli=COALESCE(NULLIF(?, ''), cli) WHERE id=?",
                (status, time.time(), " ".join(output.split())[-600:], cli, run_id),
            )
        return next((r for r in s.runs(500) if r["id"] == run_id), None)

    def runs(s, limit: int = 100) -> list[dict]:
        with s.connect() as db:
            rows = db.execute("SELECT * FROM runs ORDER BY started DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(r), "fallbacks": json.loads(r["fallbacks"]), "commands": json.loads(r["commands"] or "[]")} for r in rows]

    def interrupt_runs(s) -> int:
        with s.connect() as db:
            return db.execute("UPDATE runs SET status='interrupted', ended=? WHERE status='running'", (time.time(),)).rowcount

    def approved_folders(s):
        with s.connect() as db:
            rows = db.execute(
                "SELECT path FROM folder_requests WHERE approved_at IS NOT NULL ORDER BY path"
            ).fetchall()
        return [row["path"] for row in rows]


def engine(agent) -> str:
    """The CLI and model chain an agent runs on; the first is tried first, the rest are fallbacks."""
    chain = [agent, *getattr(agent, "fallbacks", [])]
    return " → ".join(f"{getattr(a, 'cli', 'agent')} ({getattr(a, 'model', '') or 'default model'})" for a in chain)


def lineup(cast: dict) -> dict[str, dict]:
    """Per member: the short model label and price shown on its card."""
    out = {}
    for key, agent in cast.items():
        model = getattr(agent, "model", "")
        label = "Copilot" if getattr(agent, "cli", "") == "copilot" else model.split("-")[1].title() if model.count("-") >= 2 else "Claude"
        out[key] = {"model": label, "price": getattr(agent, "price", 1.0)}
    return out


STATE = {
    "Task": lambda d: "queued",
    "Assigned": lambda d: "working",
    "Output": lambda d: "first pass done, being checked",
    "Green": lambda d: "tests passed, in review",
    "Decision": lambda d: "waiting for your decision" if d.get("escalate") else "accepted by the family" if d.get("accept") else "rejected by the family",
    "HumanDecision": lambda d: "accepted by you" if d.get("accept") else "rejected by you",
    "Applied": lambda d: "applied locally",
    "ApplyPending": lambda d: f"accepted, not applied yet ({str(d.get('reason', ''))[:80]})",
    "Blocked": lambda d: f"blocked: {d.get('need', '')}",
    "Question": lambda d: f"waiting for your answer: {str(d.get('text', ''))[:80]}",
    "HumanAnswer": lambda d: "answered, continuing",
    "Error": lambda d: "interrupted by a restart, worktree preserved" if str(d.get("message", "")).startswith("Work was interrupted") else f"error: {str(d.get('message', ''))[:80]}",
}


class Skipper:
    def __init__(s, store, agent=None):
        s.store, s.agent = store, agent

    def task_board(s, app, events=None, limit=12) -> list[dict]:
        """Every recent task with what it is, who has it, and where it stands, read from the event history."""
        events = events if events is not None else s.store.events(limit=100000)
        board: dict[str, dict] = {}
        for e in events:
            d, kind = e["data"], e["type"]
            if kind not in STATE or not isinstance(d, dict):
                continue
            t = d.get("task")
            tid = d.get("id") if kind == "Task" else t.get("id") if isinstance(t, dict) else t if isinstance(t, str) else None
            if not tid:
                continue
            row = board.setdefault(tid, {"id": tid, "task": "", "member": "", "state": ""})
            row["task"] = row["task"] or str(d.get("text") if kind == "Task" else t.get("text", "") if isinstance(t, dict) else "")[:160]
            row["member"] = d.get("member") or row["member"] if kind in {"Assigned", "Output", "Question"} else row["member"]
            row["state"] = STATE[kind](d)
        for member, ids in app.lead.active.items():
            for tid in ids:
                if tid in board:
                    board[tid].update(member=member, state="working")
        return list(board.values())[-limit:]

    def authorized(s, path):
        path = Path(path).resolve()
        return any(path == (Path(root).resolve()) or Path(root).resolve() in path.parents for root in s.store.approved_folders())

    @staticmethod
    def interrupted_tasks(events):
        decisions = {
            event["data"]["task"]["id"]
            for event in events
            if event["type"] == "Decision" and event["data"].get("task")
        }
        terminal = {
            event["data"]["task"]["id"]
            for event in events
            if event["type"] in {"Applied", "ApplyPending", "Blocked"}
            and event["data"].get("task")
        }
        terminal.update(
            event["data"]["task"]["id"]
            for event in events
            if event["type"] == "HumanDecision"
            and event["data"].get("task")
            and not event["data"].get("accept")
        )
        question_state = {}
        for event in events:
            if event["type"] in {"Question", "HumanAnswer"} and event["data"].get("task"):
                question_state[event["data"]["task"]["id"]] = event["type"]
        terminal.update(
            event["data"]["task"]["id"]
            for event in events
            if event["type"] == "Question"
            and event["data"].get("task")
            and question_state.get(event["data"]["task"]["id"]) == "Question"
        )
        return [
            event["data"]["id"]
            for event in events
            if event["type"] == "Task"
            and event["data"]["id"] not in decisions
            and event["data"]["id"] not in terminal
        ]

    @staticmethod
    def pending_apply(events):
        accepted = {
            event["data"]["task"]["id"]: event["data"]["task"]
            for event in events
            if event["type"] == "Decision"
            and event["data"].get("accept")
            and event["data"].get("task")
        }
        accepted.update(
            {
                event["data"]["task"]["id"]: event["data"]["task"]
                for event in events
                if event["type"] == "HumanDecision"
                and event["data"].get("accept")
                and event["data"].get("task")
            }
        )
        applied = {
            event["data"]["task"]["id"]
            for event in events
            if event["type"] in {"Applied", "ApplyPending"}
            and event["data"].get("task")
        }
        return [task for task_id, task in accepted.items() if task_id not in applied]

    @staticmethod
    def pending_decisions(events):
        resolved = {
            event["data"]["task"]["id"]
            for event in events
            if event["type"] == "HumanDecision" and event["data"].get("task")
        }
        return [
            event["data"]["task"]
            for event in events
            if event["type"] == "Decision"
            and event["data"].get("escalate")
            and event["data"]["task"]["id"] not in resolved
        ]

    @staticmethod
    def pending_questions(events):
        state = {}
        for event in events:
            if event["type"] in {"Question", "HumanAnswer"} and event["data"].get("task"):
                task_id = event["data"]["task"]["id"]
                state[task_id] = event["data"] if event["type"] == "Question" else None
        return [question for question in state.values() if question]

    @staticmethod
    def pending_local_apply(events):
        pending = {}
        for event in events:
            if event["type"] not in {"ApplyPending", "Applied"}:
                continue
            data = event["data"]
            task = data.get("task")
            if not isinstance(task, dict):
                continue
            task_id = task["id"]
            if event["type"] == "ApplyPending":
                pending[task_id] = task
            else:
                pending.pop(task_id, None)
        return pending

    @staticmethod
    def pending_research(events):
        jobs = {}
        for event in events:
            data = event["data"]
            if event["type"] == "ResearchRequest":
                job_id = data.get("id") or f"legacy-{event['id']}"
                jobs[job_id] = {**data, "id": job_id}
            elif event["type"] == "Finding":
                job_id = data.get("id")
                if job_id:
                    jobs.pop(job_id, None)
                else:
                    for key, job in list(jobs.items()):
                        if job.get("question") == data.get("question"):
                            jobs.pop(key)
            elif event["type"] == "Error":
                jobs.pop(data.get("task"), None)
        return list(jobs.values())

    def health(s, app):
        lead = app.lead
        events = s.store.events(limit=100000)
        research = {}
        for event in events:
            data = event["data"]
            if event["type"] == "ResearchRequest":
                job_id = data.get("id") or f"legacy-{event['id']}"
                research[job_id] = {
                    "id": job_id,
                    "question": data["question"],
                    "member": "Quill",
                    "status": "running",
                }
            elif event["type"] == "Finding":
                job_id = data.get("id") or next(
                    (key for key, job in research.items() if job["question"] == data.get("question")),
                    None,
                )
                if job_id in research:
                    research[job_id]["status"] = "completed"
                    research[job_id]["summary"] = data.get("say") or data.get("reason", "")
            elif event["type"] == "Error" and data.get("task") in research:
                research[data["task"]]["status"] = "failed"
                research[data["task"]]["summary"] = data["message"]
        active_research = [job for job in research.values() if job["status"] == "running"]
        details = lineup(app.cast)
        return {
            "running_tasks": sum(len(tasks) for tasks in lead.active.values()) + len(active_research),
            "research": list(research.values())[-20:],
            "members": [
                {
                    "key": name,
                    "name": agent.persona.name,
                    "role": agent.persona.mission.split(". ")[0],
                    **details[name],
                    "builder": name in lead.members,
                    "active": len(lead.active[name]),
                    "available": not lead.active[name] and not (name == "quill" and active_research) and not getattr(agent, "working", {}).get(agent.persona.name, 0),
                    "working": bool(getattr(agent, "working", {}).get(agent.persona.name, 0)),
                    "active_tasks": [
                        lead.tasks.get(task_id, task_id)
                        for task_id in sorted(lead.active[name])
                    ] + ([f"Research: {job['question']}" for job in active_research] if name == "quill" else []),
                    "completed_tasks": lead.completed[name],
                    "confidence": round(lead.reward[name] / lead.completed[name], 2)
                    if lead.completed[name]
                    else 0.5,
                }
                for name, agent in app.cast.items()
            ],
            "budget": {"cap": app.cast["spark"].ledger.cap, "spent": round(app.cast["spark"].ledger.spent, 4)},
        }

    def status(s, app):
        h = s.health(app)
        events = s.store.events(limit=100000)
        pending = s.pending_decisions(events)
        questions = s.pending_questions(events)
        local_apply = s.pending_local_apply(events)
        interrupted = s.interrupted_tasks(events)
        busy = [
            f"{m['name']} on {', '.join(task[:70] for task in m['active_tasks'] if not task.startswith('Research: '))}"
            for m in h["members"]
            if not m["available"] and any(not task.startswith("Research: ") for task in m["active_tasks"])
        ]
        free = [m["name"] for m in h["members"] if m["available"]]
        research = [job for job in h["research"] if job["status"] == "running"]
        learned = [
            f"{m['name']} has {m['completed_tasks']} completed task(s), {m['confidence']:.0%} recent average."
            for m in h["members"]
            if m["completed_tasks"]
        ]
        board = "; ".join(f"{r['id']} “{r['task'][:50]}” is {r['state']}" for r in s.task_board(app, events, 6))
        return (
            f"I’m watching {h['running_tasks']} active task(s). "
            f"{'Working: ' + ', '.join(busy) + '. ' if busy else ''}"
            f"{'Research: ' + '; '.join(job['id'] + ' with Quill: ' + job['question'][:90] for job in research) + '. ' if research else ''}"
            f"{'Available: ' + ', '.join(free) + '. ' if free else ''}"
            f"{'Performance: ' + ' '.join(learned) + ' ' if learned else ''}"
            f"{'Needs your decision on task(s): ' + ', '.join(t['id'] for t in pending) + '. ' if pending else ''}"
            f"{'Waiting for your answer on task(s): ' + ', '.join(q['task']['id'] for q in questions) + '. ' if questions else ''}"
            f"{'Accepted work ready to apply locally: ' + ', '.join(local_apply) + '. ' if local_apply else ''}"
            f"{'Interrupted task(s), safely preserved: ' + ', '.join(interrupted) + '. Say “resume” to continue the latest. ' if interrupted else ''}"
            f"{'Recent tasks: ' + board + '. ' if board else ''}"
            f"Estimated spend today ${h['budget']['spent']:.3f} of ${h['budget']['cap']:.2f} (rough: characters/4 × list price). "
            f"Local state is saved on this computer. I’ll tell you when work is assigned, blocked, ready, or needs your decision."
        )

    def folders_for(s, text: str, task: str = "") -> list[str]:
        """Folders named as paths in the message or the planned task, else local projects by name."""
        found = list(dict.fromkeys([*folders_in(text), *folders_in(task)]))
        if found:
            return found
        if LOCAL_HINT.search(text) or LOCAL_HINT.search(task):
            return local_projects(f"{text} {task}", s.store.approved_folders())
        return []

    def member_context(s, app, key: str) -> tuple[str, list[str]]:
        """The member's current tasks (id, text, worktree) and open questions, plus the worktree folders."""
        lead = app.lead
        ids = sorted(lead.active[key])
        waiting = [q for q in s.pending_questions(s.store.events(limit=100000)) if q.get("member") == key]
        ids += [q["task"]["id"] for q in waiting if q["task"]["id"] not in ids]
        asked = {q["task"]["id"]: q["text"] for q in waiting}
        lines, dirs = [], []
        for task_id in ids:
            tree = lead.root / "work" / task_id
            if tree.is_dir():
                dirs.append(str(tree))
            note = f" Waiting on the user: {asked[task_id]}" if task_id in asked else ""
            lines.append(f"- task {task_id}: {lead.tasks.get(task_id, '')[:600]} (worktree {tree}){note}")
        if not lines:
            return f"{app.cast[key].persona.name} has no task in progress right now.", dirs
        return f"{app.cast[key].persona.name}'s current tasks (you are working on these; answer questions about them from this list):\n" + "\n".join(lines), dirs

    def consult_plan(s, app, members: str, repo: str, task_id: str) -> tuple[list[str], str, str]:
        keys = [k for k in dict.fromkeys(re.findall(r"[a-z]+", members.lower())) if k in app.cast]
        if not keys:
            raise ValueError("Tell me which family member to ask, for example “ask Vera to verify task 1234abcd”.")
        tree = app.lead.root / "work" / task_id if task_id else None
        found = task_id if tree and tree.is_dir() else ""
        return keys, str(tree) if found else repo, found

    async def consult(s, app, keys: list[str], ask: str, cwd: str, task_id: str) -> None:
        """Members work in parallel in the task's worktree; each finding is reported to the user as it arrives."""
        text = app.lead.tasks.get(task_id, "") if task_id else ""

        async def one(key):
            agent = app.cast[key]
            prompt = (
                f"Skipper asked you, {agent.persona.name}, to help verify work. Run whatever tests or checks you need "
                "(pytest, lint, typecheck, build, a browser pass) but do not change source files. "
                "Report briefly: what you ran, the results, and any problems with how to fix them.\n"
                f"{'Task under review: ' + text + chr(10) if text else ''}Request: {ask}"
            )
            token = ACCESS.set(Access((cwd,)))
            try:
                out = (await agent.ask(prompt, cwd)).strip()
            except Exception as e:  # noqa: BLE001 - always report back, even a failure
                out = f"I could not finish: {type(e).__name__}: {e}"[:500]
            finally:
                ACCESS.reset(token)
            await app.bus.emit(Consulted(task_id or uuid.uuid4().hex[:8], key, ask, out[:4000]))

        await asyncio.gather(*(one(k) for k in keys))

    def no_folder_reply(s, text: str) -> str:
        return ("That sounds like a local project, but I couldn’t find a matching folder in the approved folders or the usual "
                "project locations, so I haven’t started a web search. Send me its full path (for example /home/you/Projects/name).")

    def report_for(s, job: str) -> str:
        """The latest full report Skipper stored for a task or research job id."""
        rows = [m for m in s.store.messages(limit=2000) if m.get("task") == job and m["role"] == "skipper" and not m["text"].startswith("ℹ️")]
        reports = [m for m in rows if "\n" in m["text"] or "finished" in m["text"] or "failed" in m["text"]]
        return (reports or rows)[-1]["text"] if (reports or rows) else ""

    def direct_answer(s, text: str, app) -> tuple[str, str] | None:
        """Questions answerable from state or config, without a model call: job ids, member findings, models."""
        if m := MODEL_Q.search(text):
            key = (m.group(1) or m.group(2) or "").lower()
            agent = app.cast.get(key) or next((a for a in app.cast.values() if a.persona.name.lower() == key), None)
            if agent is not None:
                chain = [agent, *getattr(agent, "fallbacks", [])]
                hops = " → ".join(f"{a.cli}" + (f" ({a.model})" if getattr(a, "model", "") else "") for a in chain)
                return f"{agent.persona.name} uses {hops}. The first entry is tried first; the rest are fallbacks.", ""
        events = None
        for job in JOB_ID.findall(text.lower()):
            if report := s.report_for(job):
                return report, job
            events = events if events is not None else s.store.events(limit=100000)
            if any(isinstance(e["data"], dict) and job in (e["data"].get("id"), (e["data"].get("task") or {}).get("id") if isinstance(e["data"].get("task"), dict) else e["data"].get("task")) for e in events):
                done = {"Finding", "Decision", "Applied", "Error"}
                state = [e["type"] for e in events if isinstance(e["data"], dict) and job in (e["data"].get("id"), e["data"].get("task") if isinstance(e["data"].get("task"), str) else (e["data"].get("task") or {}).get("id"))]
                return (f"Job {job} has no final report yet (latest event: {state[-1]})." if not done & set(state) else f"Job {job} finished ({state[-1]}) but no report was stored."), job
        if (m := FOUND.search(text)) and (who := m.group(1).lower()) not in {"you", "i", "we", "it", "they"}:
            agent = app.cast.get(who) or next((a for a in app.cast.values() if a.persona.name.lower() == who), None)
            if agent is not None:
                name = agent.persona.name
                rows = [x for x in s.store.messages(limit=2000) if x["role"] == "skipper" and x.get("task") and x["text"].startswith(name) and "\n" in x["text"]]
                if rows:
                    return rows[-1]["text"], rows[-1]["task"]
                return f"I don’t have a finished report from {name} yet.", ""
        return None

    def respond(s, user, text, task=None, role="skipper", **extra):
        assistant = s.store.message(role, text, task)
        return {"reply": text, "task": task, **extra, "messages": [user, assistant]}

    async def talk(s, app, key, ask, text, user):
        """A conversation with one member: read-only, unless the user asks for changes in a named folder."""
        agent = app.cast[key]
        context, trees = s.member_context(app, key)
        folders = [*s.folders_for(text), *trees]
        write = bool(folders) and bool(WRITE_ASK.search(ask))
        rule = "You may read, create, edit and delete files in the named folders to do what the user asked; report exactly what you changed." if write else "You may run read-only inspection commands. Do not edit files or start implementation work."
        prompt = f"Answer the user conversationally as {agent.persona.name}. {rule}\n{context}\nUser: {ask}"
        reply = await read_only(agent, prompt, lobby(), folders, write)
        return s.respond(user, f"{agent.persona.name}: {reply}", role="member")

    async def chat(s, text, repo, app, enqueue):
        text = text.strip()
        if not text:
            raise ValueError("Please tell me what you need.")
        context = s.store.messages(limit=12)
        user = s.store.message("user", text)
        lower = text.lower()
        resume = re.fullmatch(r"resume(?: task)?(?:\s+([a-z0-9+-]+))?", lower)
        if resume:
            records = s.store.events(limit=100000)
            interrupted = s.interrupted_tasks(records)
            wanted = resume.group(1) or (interrupted[-1] if interrupted else "")
            if wanted not in interrupted:
                raise ValueError("I can’t find an interrupted task to resume. Ask “status” or give me a new task.")
            stored = next(
                event["data"]
                for event in records
                if event["type"] == "Task" and event["data"]["id"] == wanted
            )
            task = Task(**stored)
            assigned = next(
                (
                    event["data"]["member"]
                    for event in records
                    if event["type"] == "Assigned" and event["data"]["task"]["id"] == wanted
                ),
                "",
            )
            member = assigned if assigned in app.lead.members else app.lead.select(task.text)
            enqueue(app.lead.resume(task, member), wanted)
            reply = f"I’ve resumed task {wanted} with {app.cast[member].persona.name} in its preserved worktree."
            return s.respond(user, reply, wanted, member=member)
        decision = re.fullmatch(r"(accept|reject)(?: task)?\s+([a-z0-9+-]+)", lower)
        if decision:
            choice, wanted = decision.groups()
            pending = {
                task["id"]: task
                for task in s.pending_decisions(s.store.events(limit=100000))
            }
            if wanted not in pending:
                raise ValueError(f"Task {wanted} is not waiting for a family decision.")
            task = Task(**pending[wanted])
            enqueue(app.lead.resolve(task, choice == "accept"), wanted)
            reply = f"I’ve recorded your decision to {choice} task {wanted}. I’ll report the result."
            return s.respond(user, reply, wanted, member=None)
        apply = re.fullmatch(r"apply(?: task)?\s+([a-z0-9+-]+)", lower)
        if apply:
            wanted = apply.group(1)
            pending = s.pending_local_apply(s.store.events(limit=100000))
            if wanted not in pending:
                raise ValueError(f"Task {wanted} has no accepted local changes waiting to apply.")
            task = Task(**pending[wanted])
            enqueue(app.lead.apply(task), wanted)
            reply = f"I’m checking whether the accepted changes for task {wanted} can now be applied safely."
            return s.respond(user, reply, wanted, member=None)
        if re.fullmatch(r"\s*(?:list |show |what are )?(?:my |your )?(?:habits|preferences)\??\s*", lower):
            return s.respond(user, app.profile.report())
        if forget := re.fullmatch(r"\s*forget(?: habit)?\s+(\d+)\s*", lower):
            return s.respond(user, f"Dropped: {app.profile.forget(int(forget.group(1)))}")
        if re.fullmatch(r"\s*learn (?:from )?(?:my |how i )(?:work|commits|habits|code|requests)\s*[.!]?", lower):
            enqueue(app.profile.study())
            return s.respond(user, "I’m studying your recent commits and requests now. I’ll tell you what I notice.")
        if re.fullmatch(r"\s*(?:list |show |what )?(?:our |the )?skills\??\s*", lower):
            return s.respond(user, skills.report(app.lead.root))
        if (moved := MOVE.match(text)) and len(text) < 300:
            named = folders_in(moved.group(1)) or local_projects(moved.group(1), s.store.approved_folders())
            if named:
                path = safe_folder(named[0])
                s.store.grant_folder(str(path))
                items = sorted(p.name + ("/" if p.is_dir() else "") for p in path.iterdir() if not p.name.startswith("."))
                kind = "a git repository" if (path / ".git").exists() else "a plain folder"
                reply = f"Now working in {path} ({kind}), {len(items)} items: {', '.join(items[:12])}{'…' if len(items) > 12 else ''}. Tasks I start here can read and write in it."
                return s.respond(user, reply, repo=str(path))
        if answered := s.direct_answer(text, app):
            return s.respond(user, answered[0], answered[1] or None)
        if is_research_followup(text):
            jobs = s.health(app)["research"]
            active = [job for job in jobs if job["status"] == "running"]
            if active:
                reply = "Quill is still researching:\n" + "\n".join(
                    f"- Job {job['id']}: {job['question']}" for job in active
                )
            else:
                finished = [job for job in jobs if job["status"] == "completed"]
                failed = [job for job in jobs if job["status"] == "failed"]
                if finished:
                    job = finished[-1]
                    reply = s.report_for(job["id"]) or f"Quill finished job {job['id']}: {job['summary'] or job['question']}"
                elif failed:
                    job = failed[-1]
                    reply = f"Quill’s latest research job {job['id']} failed: {job['summary']}"
                else:
                    reply = "I don’t have a research job recorded yet, so I can’t claim Quill has started. Tell me what you want researched and I’ll assign it."
            return s.respond(user, reply)
        if len(text) < 80 and re.search(r"\b(status|health|who is working|what is happening|what's happening)\b", lower):
            return s.respond(user, s.status(app))
        if re.search(r"\b(capabilities|what can (?:the )?(?:family|fam|members)|what does .+ do)\b", lower):
            return s.respond(user, "Here’s what each family member is for:\n" + "\n".join(
                f"- {agent.persona.name}: {agent.persona.mission.split('. ')[0]} [{engine(agent)}]"
                for agent in app.cast.values()
            ))
        if re.fullmatch(r"\s*(help|what can you do)\s*[?.!]*", lower):
            return s.respond(user, "Tell me the outcome you want and I’ll assign the right family member, track the work and checks, then report back. Ask “status” for a health summary, or address a member by name if you want to speak with them.")
        if re.fullmatch(r"\s*(hi|hello|hey)\s*[?.!]*", lower):
            return s.respond(user, "Hello. Tell me what outcome you want, and I’ll take it from there.")
        if lower.startswith("approve "):
            name = text[8:].strip()
            if name not in app.linker.props:
                raise ValueError(f"I can’t find a waiting proposal named {name}.")
            await app.bus.emit(Approve(name))
            return s.respond(user, f"I approved {name}. I’ll keep it available for tasks that need it.")
        if asked := NEW_RESEARCH_ASK.search(text):
            question = re.sub(r"^\s*(this|that|it)?\s*[:,-]?\s*", "", text[asked.end():]) or text
            folders = s.folders_for(text, question)
            if not folders and LOCAL_HINT.search(text):
                return s.respond(user, s.no_folder_reply(text))
            research = ResearchRequest(question, folders)
            enqueue(research, research.id)
            return s.respond(user, f"I’ve assigned this research to Quill (job {research.id}). I’ll show its progress here and report the findings.", research=research.id)
        for key, agent in app.cast.items():
            prefix = re.match(rf"^(?:ask|tell|talk to)\s+{re.escape(agent.persona.name)}\s*[:,]?\s*", text, re.IGNORECASE)
            if prefix:
                ask = text[prefix.end():].strip() or text
                if CONSULT_ASK.search(ask):
                    task_id = (re.search(r"\b([0-9a-f]{8}\+*)(?![\w+])", ask) or [None, ""])[1]
                    keys, cwd, found = s.consult_plan(app, agent.persona.name, repo, task_id)
                    enqueue(s.consult(app, keys, ask, cwd, found), found)
                    return s.respond(user, f"I’ve asked {agent.persona.name} to check this in the background and I’ll report the findings here when they’re done.", found or None)
                return await s.talk(app, key, ask, text, user)
        members = [
            {
                "key": key,
                "name": agent.persona.name,
                "capability": agent.persona.mission,
                "active": len(app.lead.active[key]),
                "running": [{"id": i, "task": app.lead.tasks.get(i, "")[:200]} for i in sorted(app.lead.active[key])],
                "completed": app.lead.completed[key],
                "success_rate": app.lead.reward[key] / app.lead.completed[key] if app.lead.completed[key] else 0.5,
                "can_build": key in app.lead.members,
            }
            for key, agent in app.cast.items()
        ]
        tools = {
            "trusted": [
                {"name": name, "description": tool["desc"], "ready": ready(tool["cfg"])}
                for name, tool in app.linker.trusted.items()
            ],
            "proposals": list(app.linker.props),
            "built_in": ["local git worktrees", "pytest", "family review", "research", "local persistent memory"],
        }
        questions = s.pending_questions(s.store.events(limit=100000))
        plan = await s.agent.ask_json(
            f"{PLAN}\nFamily and workload: {json.dumps(members)}\nTools and proposals: {json.dumps(tools)}"
            f"\nQuestions waiting for the user: {json.dumps(questions)}"
            f"\nTasks and where each stands: {json.dumps(s.task_board(app))}"
            f"\nRecent conversation: {json.dumps(context[-10:])}\nCurrent repository: {repo}\nUser message: {text}",
            lobby(),
        )
        plan = clean_plan(plan)
        intent = plan["intent"]
        reply = plan["reply"]
        if intent == "status":
            reply = s.status(app)
        elif intent == "task":
            task_text = str(plan.get("task") or text).strip()
            member = str(plan.get("member", "")).strip().lower()
            lightest_load = min(len(app.lead.active[name]) for name in app.lead.members)
            if member not in app.lead.members or len(app.lead.active[member]) > lightest_load:
                member = app.lead.select(task_text)
            task = Task(task_text, cwd=repo, repo=repo, pin=member)
            enqueue(task)
            return s.respond(user, f"I’ve assigned this to {app.cast[member].persona.name}. I’ll track the work and checks, and tell you if I need your input.", task.id, member=member)
        elif intent == "member_chat":
            member = str(plan.get("member", "")).strip().lower()
            if member not in app.cast:
                raise ValueError("Skipper could not identify which family member you want to speak with.")
            return await s.talk(app, member, text, text, user)
        elif intent == "consult":
            task_id = str(plan.get("name", "")).strip()
            keys, cwd, found = s.consult_plan(app, str(plan.get("member", "")), repo, task_id)
            enqueue(s.consult(app, keys, str(plan.get("task") or text), cwd, found), found)
            names = ", ".join(app.cast[k].persona.name for k in keys)
            return s.respond(user, reply or f"I’ve asked {names} to check this in the background and I’ll report their findings here when they’re done.", found or None)
        elif intent == "note":
            running = [i for ids in app.lead.active.values() for i in ids]
            wanted = str(plan.get("name", "")).strip()
            wanted = wanted if wanted in app.lead.tasks else running[0] if len(running) == 1 else ""
            if not wanted:
                raise ValueError("Which task is that for? Send its id, for example “tell task 1234abcd: use the existing form component”.")
            app.lead.tell(wanted, "the user", str(plan.get("task") or text))
            who = next((app.cast[m].persona.name for m, ids in app.lead.active.items() if wanted in ids), "the builder")
            return s.respond(user, reply or f"Passed that to {who} on task {wanted}. They get it before their next step, even mid-build.", wanted)
        elif intent == "answer_question":
            pending = {
                question["task"]["id"]: question
                for question in questions
            }
            wanted = str(plan.get("name", "")).strip()
            if wanted not in pending:
                if len(pending) != 1:
                    raise ValueError("Skipper needs the task ID for the question you are answering.")
                wanted = next(iter(pending))
            question = pending[wanted]
            stored = next(
                event["data"]
                for event in s.store.events(limit=100000)
                if event["type"] == "Task" and event["data"]["id"] == wanted
            )
            task = Task(**stored)
            answer = str(plan.get("task") or text).strip()
            async def continue_task():
                await app.bus.emit(HumanAnswer(task, answer))
                await app.lead.answer(task, question["member"], answer)

            enqueue(continue_task(), wanted)
            return s.respond(user, f"Thanks. I’ve passed your answer to {app.cast[question['member']].persona.name} so they can continue task {wanted}.", wanted, member=question["member"])
        elif intent == "research":
            folders = s.folders_for(text, str(plan.get("task") or ""))
            if not folders and LOCAL_HINT.search(text):
                return s.respond(user, s.no_folder_reply(text))
            research = ResearchRequest(str(plan.get("task") or text), folders)
            enqueue(research, research.id)
            reply = reply or f"I’ve assigned this research to Quill (job {research.id}). I’ll show its progress here and report the findings."
        elif intent == "focus":
            enqueue(Focus(str(plan.get("task") or text)))
            reply = reply or "I’ve updated the family’s focus and started research."
        elif intent == "teach":
            enqueue(Correction(str(plan.get("task") or text)))
            reply = reply or "I’ve saved that guidance for future work."
        elif intent == "approve_tool":
            name = str(plan.get("name", "")).strip()
            if name not in app.linker.props:
                raise ValueError(f"I can’t find a waiting proposal named {name}.")
            await app.bus.emit(Approve(name))
            reply = reply or f"I approved {name}. I’ll keep it available for tasks that need it."
        elif intent != "answer":
            raise ValueError("Skipper could not determine a safe next step. Please rephrase the request.")
        elif not reply:
            raise ValueError("Skipper could not answer that just now.")
        return s.respond(user, reply)
