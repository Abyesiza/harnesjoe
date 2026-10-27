import json
import re
import sqlite3
import time
import uuid
from dataclasses import asdict, is_dataclass
from pathlib import Path

from core import ACCESS, Access, Approve, HumanAnswer, ResearchRequest, Task
from link import ready

INTENTS = {"task", "status", "member_chat", "answer_question", "research", "focus", "teach", "approve_tool", "answer"}
LIMITS = {"intent": 40, "task": 8000, "member": 40, "reply": 4000, "name": 200}
SYSTEM = {"/", "/bin", "/boot", "/dev", "/etc", "/lib", "/lib32", "/lib64", "/opt", "/proc", "/root", "/run", "/sbin", "/srv", "/sys", "/tmp", "/usr", "/var", "/home", "/snap", "/mnt", "/media"}
SENSITIVE = {".ssh", ".gnupg", ".aws", ".kube", ".password-store", ".fam"}
QUOTED = re.compile(r"[\"'`“”‘’]((?:~|/)[^\"'`“”‘’\n]+)[\"'`“”‘’]")
PATH_START = re.compile(r"(?<![\w/])(?:~/|/)")
TRAILING = ".,:;!?)]}"


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
        raise ValueError("Use an absolute folder path, such as /home/you/Projects/app.")
    path = path.resolve()
    if not path.is_dir():
        raise ValueError(f"{path} is not an accessible directory.")
    if str(path) in SYSTEM or path == Path.home().resolve():
        raise ValueError(f"{path} is too broad or a system folder; choose a project folder inside it.")
    if any(part in SENSITIVE for part in path.parts) or any(str(path).startswith(s + "/") for s in SYSTEM - {"/", "/home", "/tmp", "/mnt", "/media", "/srv", "/opt"}):
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
    candidate = Path(raw.strip().rstrip(TRAILING)).expanduser()
    return str(candidate) if candidate.is_absolute() and candidate.is_dir() else None


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


async def read_only(agent, prompt: str, cwd: str, folders: list[str]) -> str:
    """Ask a member a question with read-only access to the folders the user named."""
    token = ACCESS.set(Access(tuple(folders), readonly=True))
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
FOUND = re.compile(r"\bwhat (?:did |has |have )?(\w+) (?:find|found|say|said|conclude|concluded|report|reported)\b", re.I)
MODEL_Q = re.compile(r"\b(?:which|what) (?:model|llm|cli)s?\b.*?\b(?:does|do|is|for|of)\s+(\w+)|\b(\w+)(?:'s|’s) model\b", re.I)
LOCAL_HINT = re.compile(r"\b(project|folder|foldr|repo|repository|codebase|directory|dir)\b", re.I)
SEARCH_ROOTS = ("Desktop", "Desktop/my_projects", "Downloads", "Projects", "projects", "code", "src", "dev", "work", "repos", "")
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
    for root in SEARCH_ROOTS:
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


def folder_in(text: str) -> str | None:
    """The first directory named in a chat message, if any."""
    found = folders_in(text)
    return found[0] if found else None

PLAN = (
    "You are Skipper, the user's conversational chief of staff for an autonomous software family. "
    "Classify the request and decide the next safe step. Only assign implementation work after the user asks for a concrete software outcome. "
    "Questions should be answered directly. Do not invent that work, tests, or approvals happened. "
    "Choose only one intent: task, status, member_chat, answer_question, research, focus, teach, approve_tool, or answer. "
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

    def request_folder(s, path):
        with s.connect() as db:
            db.execute(
                "INSERT INTO folder_requests (path, requested_at, approved_at) VALUES (?, ?, NULL) "
                "ON CONFLICT(path) DO UPDATE SET requested_at=excluded.requested_at",
                (path, time.time()),
            )
            return db.execute("SELECT id, path FROM folder_requests WHERE path=?", (path,)).fetchone()

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


class Skipper:
    def __init__(s, store, agent=None):
        s.store, s.agent = store, agent

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
        return {
            "running_tasks": sum(len(tasks) for tasks in lead.active.values()) + len(active_research),
            "research": list(research.values())[-20:],
            "members": [
                {
                    "key": name,
                    "name": agent.persona.name,
                    "role": agent.persona.mission.split(". ")[0],
                    "builder": name in lead.members,
                    "active": len(lead.active[name]),
                    "available": not lead.active[name] and not (name == "quill" and active_research),
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
            "state_file": str(s.store.path),
        }

    def status(s, app):
        h = s.health(app)
        events = s.store.events(limit=100000)
        pending = s.pending_decisions(events)
        questions = s.pending_questions(events)
        local_apply = s.pending_local_apply(events)
        interrupted = s.interrupted_tasks(events)
        busy = [
            f"{m['name']} on {', '.join(task[:70] for task in m['active_tasks'])}"
            for m in h["members"]
            if not m["available"]
        ]
        free = [m["name"] for m in h["members"] if m["available"]]
        research = [job for job in h["research"] if job["status"] == "running"]
        learned = [
            f"{m['name']} has {m['completed_tasks']} completed task(s), {m['confidence']:.0%} recent average."
            for m in h["members"]
            if m["completed_tasks"]
        ]
        return (
            f"I’m watching {h['running_tasks']} active task(s). "
            f"{'Working: ' + ', '.join(busy) + '. ' if busy else ''}"
            f"{'Research: ' + '; '.join(job['id'] + ' with Quill: ' + job['question'] for job in research) + '. ' if research else ''}"
            f"{'Available: ' + ', '.join(free) + '. ' if free else ''}"
            f"{'Performance: ' + ' '.join(learned) + ' ' if learned else ''}"
            f"{'Needs your decision on task(s): ' + ', '.join(t['id'] for t in pending) + '. ' if pending else ''}"
            f"{'Waiting for your answer on task(s): ' + ', '.join(q['task']['id'] for q in questions) + '. ' if questions else ''}"
            f"{'Accepted work ready to apply locally: ' + ', '.join(local_apply) + '. ' if local_apply else ''}"
            f"{'Interrupted task(s), safely preserved: ' + ', '.join(interrupted) + '. Say “resume” to continue the latest. ' if interrupted else ''}"
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
            assistant = s.store.message("skipper", reply, wanted)
            return {"reply": reply, "task": wanted, "member": member, "messages": [user, assistant]}
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
            assistant = s.store.message("skipper", reply, wanted)
            return {"reply": reply, "task": wanted, "member": None, "messages": [user, assistant]}
        apply = re.fullmatch(r"apply(?: task)?\s+([a-z0-9+-]+)", lower)
        if apply:
            wanted = apply.group(1)
            pending = s.pending_local_apply(s.store.events(limit=100000))
            if wanted not in pending:
                raise ValueError(f"Task {wanted} has no accepted local changes waiting to apply.")
            task = Task(**pending[wanted])
            enqueue(app.lead.apply(task), wanted)
            reply = f"I’m checking whether the accepted changes for task {wanted} can now be applied safely."
            assistant = s.store.message("skipper", reply, wanted)
            return {"reply": reply, "task": wanted, "member": None, "messages": [user, assistant]}
        if answered := s.direct_answer(text, app):
            assistant = s.store.message("skipper", answered[0], answered[1] or None)
            return {"reply": answered[0], "task": answered[1] or None, "messages": [user, assistant]}
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
            assistant = s.store.message("skipper", reply)
            return {"reply": reply, "task": None, "messages": [user, assistant]}
        if re.search(r"\b(status|health|who is working|what is happening|what's happening)\b", lower):
            reply = s.status(app)
            assistant = s.store.message("skipper", reply)
            return {"reply": reply, "task": None, "messages": [user, assistant]}
        if re.search(r"\b(capabilities|what can (?:the )?(?:family|fam|members)|what does .+ do)\b", lower):
            reply = "Here’s what each family member is for:\n" + "\n".join(
                f"- {agent.persona.name}: {agent.persona.mission.split('. ')[0]}"
                for agent in app.cast.values()
            )
            assistant = s.store.message("skipper", reply)
            return {"reply": reply, "task": None, "messages": [user, assistant]}
        if re.fullmatch(r"\s*(help|what can you do)\s*[?.!]*", lower):
            reply = "Tell me the outcome you want and I’ll assign the right family member, track the work and checks, then report back. Ask “status” for a health summary, or address a member by name if you want to speak with them."
            assistant = s.store.message("skipper", reply)
            return {"reply": reply, "task": None, "messages": [user, assistant]}
        if re.fullmatch(r"\s*(hi|hello|hey)\s*[?.!]*", lower):
            reply = "Hello. Tell me what outcome you want, and I’ll take it from there."
            assistant = s.store.message("skipper", reply)
            return {"reply": reply, "task": None, "messages": [user, assistant]}
        if lower.startswith("approve "):
            name = text[8:].strip()
            if name not in app.linker.props:
                raise ValueError(f"I can’t find a waiting proposal named {name}.")
            await app.bus.emit(Approve(name))
            reply = f"I approved {name}. I’ll keep it available for tasks that need it."
            assistant = s.store.message("skipper", reply)
            return {"reply": reply, "task": None, "messages": [user, assistant]}
        if asked := NEW_RESEARCH_ASK.search(text):
            question = re.sub(r"^\s*(this|that|it)?\s*[:,-]?\s*", "", text[asked.end():]) or text
            folders = s.folders_for(text, question)
            if not folders and LOCAL_HINT.search(text):
                reply = s.no_folder_reply(text)
                assistant = s.store.message("skipper", reply)
                return {"reply": reply, "task": None, "messages": [user, assistant]}
            research = ResearchRequest(question, folders)
            enqueue(research, research.id)
            reply = f"I’ve assigned this research to Quill (job {research.id}). I’ll show its progress here and report the findings."
            assistant = s.store.message("skipper", reply)
            return {"reply": reply, "task": None, "research": research.id, "messages": [user, assistant]}
        for key, agent in app.cast.items():
            prefix = re.match(rf"^(?:ask|tell|talk to)\s+{re.escape(agent.persona.name)}\s*[:,]?\s*", text, re.IGNORECASE)
            if prefix:
                ask = text[prefix.end():].strip() or text
                prompt = f"Answer the user conversationally as {agent.persona.name}. You may run read-only inspection commands inside the approved folders. Do not edit files or start implementation work.\nUser: {ask}"
                reply = await read_only(agent, prompt, lobby(), s.folders_for(text))
                assistant = s.store.message("member", f"{agent.persona.name}: {reply}")
                return {"reply": assistant["text"], "task": None, "messages": [user, assistant]}
        members = [
            {
                "key": key,
                "name": agent.persona.name,
                "capability": agent.persona.mission,
                "active": len(app.lead.active[key]),
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
            reply = f"I’ve assigned this to {app.cast[member].persona.name}. I’ll track the work and checks, and tell you if I need your input."
            assistant = s.store.message("skipper", reply, task.id)
            return {"reply": reply, "task": task.id, "member": member, "messages": [user, assistant]}
        elif intent == "member_chat":
            member = str(plan.get("member", "")).strip().lower()
            if member not in app.cast:
                raise ValueError("Skipper could not identify which family member you want to speak with.")
            prompt = f"Answer the user conversationally as {app.cast[member].persona.name}. You may run read-only inspection commands inside the approved folders. Do not edit files.\nUser: {text}"
            reply = await read_only(app.cast[member], prompt, lobby(), s.folders_for(text))
            assistant = s.store.message("member", f"{app.cast[member].persona.name}: {reply}")
            return {"reply": assistant["text"], "task": None, "messages": [user, assistant]}
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
            reply = f"Thanks. I’ve passed your answer to {app.cast[question['member']].persona.name} so they can continue task {wanted}."
            assistant = s.store.message("skipper", reply, wanted)
            return {"reply": reply, "task": wanted, "member": question["member"], "messages": [user, assistant]}
        elif intent == "research":
            folders = s.folders_for(text, str(plan.get("task") or ""))
            if not folders and LOCAL_HINT.search(text):
                reply = s.no_folder_reply(text)
                assistant = s.store.message("skipper", reply)
                return {"reply": reply, "task": None, "messages": [user, assistant]}
            research = ResearchRequest(str(plan.get("task") or text), folders)
            enqueue(research, research.id)
            reply = reply or f"I’ve assigned this research to Quill (job {research.id}). I’ll show its progress here and report the findings."
        elif intent == "focus":
            from core import Focus

            enqueue(Focus(str(plan.get("task") or text)))
            reply = reply or "I’ve updated the family’s focus and started research."
        elif intent == "teach":
            from core import Correction

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
        assistant = s.store.message("skipper", reply)
        return {"reply": reply, "task": None, "messages": [user, assistant]}
