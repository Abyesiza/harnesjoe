import os
import re
from pathlib import Path


def load_env(path):
    for ln in path.read_text().splitlines() if path.exists() else []:
        k, _, v = ln.partition("=")
        if k.strip() and not k.lstrip().startswith("#"):
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


load_env(Path(__file__).with_name(".env"))

import asyncio
import hmac
import json
import logging
import secrets
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import asdict, is_dataclass
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

import core
from agents import Agent
from app import MODEL, P, build
from core import (
    CURRENT_TASK,
    Approve,
    Correction,
    Focus,
    ResearchRequest,
    Task,
    Tick,
    Ticker,
)
import reports
import uploads
import urllib.parse
from link import ready
from skipper import Skipper, StateStore, folders_in, safe_folder

log = logging.getLogger("fam.server")

WEB = Path(__file__).with_name("web")
S = SimpleNamespace(app=None, feed=None, store=None, skipper=None, ticker=None, every=None, jobs=set(), token="")
TOKEN_HEADER = "x-fam-token"


class Feed:
    def __init__(s, bus, store):
        s.store, s.log, s.waiters = store, deque(store.events(), maxlen=1000), set()
        s.n = s.log[-1]["id"] if s.log else 0
        for cls in vars(core).values():
            if isinstance(cls, type) and is_dataclass(cls) and cls is not Tick:
                bus.subs[cls].append(s.tap(cls.__name__))

    def tap(s, name):
        async def handle(e):
            s.push(name, asdict(e))

        return handle

    def push(s, kind, data):
        ev = s.store.event(kind, data)
        s.n = ev["id"]
        s.log.append(ev)
        task = data.get("task", {}) if isinstance(data, dict) else {}
        task_id = task.get("id", "") if isinstance(task, dict) else ""
        summaries = {
            "Assigned": lambda d: f"{d['member']} is taking task {d['task']['id']}: {d['task']['text'][:100]}",
            "Green": lambda d: f"The tests passed for task {d['task']['id']}; the family is reviewing it.",
            "Decision": lambda d: (
                f"Task {d['task']['id']} was sent for your decision. Send “accept task {d['task']['id']}” or “reject task {d['task']['id']}”."
                if d["escalate"]
                else f"Task {d['task']['id']} was {'accepted' if d['accept'] else 'rejected'} by the family."
            ),
            "Blocked": lambda d: f"Task {d['task']['id']} is blocked: {d['need']}.",
            "Applied": lambda d: f"Accepted changes for task {d['task']['id']} were applied locally at {d['path']}. Nothing was pushed.",
            "ApplyPending": lambda d: f"Task {d['task']['id']}: {d['reason']} Worktree: {d['task']['cwd']}",
            "HumanDecision": lambda d: f"You chose to {'accept' if d['accept'] else 'reject'} task {d['task']['id']}.",
            "Question": lambda d: f"{d['member']} needs your input on task {d['task']['id']}: {d['text']}",
            "HumanAnswer": lambda d: (
                f"Skipper answered the family’s question on task {d['task']['id']} for you."
                if d["answer"].startswith("(Skipper")
                else f"You answered the family’s question on task {d['task']['id']}."
            ),
            "Error": lambda d: (
                f"Task {d.get('task', '')} was interrupted by a Skipper restart. Its worktree is preserved; send “resume task {d.get('task', '')}” to continue."
                if d["message"].startswith("Work was interrupted by a Skipper restart.")
                else f"Task {d.get('task', '')} needs attention: {d['message']}"
            ),
            "Said": lambda d: f"{d['name']}: {d['text']}",
            "Consulted": lambda d: f"{d['member'].title()} reported back on “{d['ask'][:120]}” (task {d['id']}):\n{d['findings']}",
        }
        if kind in summaries or kind in ("Finding", "Scouted"):
            s.store.message("skipper", s.report(kind, data, summaries), task_id or data.get("id") or None)
        for q in s.waiters:
            q.put_nowait(ev)


    def report(s, kind, data, summaries):
        """Turn a finished event into a structured, readable report for Joel."""
        def runs(tid):
            return [r for r in s.store.runs(500) if tid and r["task"] == tid]
        try:
            if kind == "Finding":
                return reports.research(data, runs(data.get("id")))
            if kind == "Scouted":
                return reports.scouted(data)
            first = summaries[kind](data)
            if kind == "Decision":
                return reports.decision(data, runs(data["task"]["id"]), first)
            if kind == "Error" and not data["message"].startswith("Work was interrupted"):
                return reports.error(data, runs(data.get("task", "")), first)
            return first
        except (KeyError, TypeError, ValueError) as e:  # a report must never break the feed
            log.warning("report for %s failed: %s", kind, e)
            return summaries[kind](data) if kind in summaries else f"{kind} finished."


class RunTracker:
    """Persists every CLI call and streams its lifecycle to the UI."""

    def start(self, cli: str, cwd: str, prompt: str) -> str:
        row = S.store.run_start(cli, cwd, prompt, CURRENT_TASK.get())
        S.feed.push("Run", row)
        return row["id"]

    def fallback(self, run_id: str, failed: str, to: str, reason: str) -> None:
        S.store.run_fallback(run_id, failed, to, reason)
        S.feed.push("Fallback", {"run": run_id, "task": CURRENT_TASK.get(), "failed": failed, "to": to, "reason": reason})

    def __init__(self) -> None:
        self.noticed: set[tuple[str, str]] = set()

    def command(self, run_id: str, entry: dict) -> None:
        S.store.run_command(run_id, entry)
        S.feed.push("Command", {"run": run_id, "task": CURRENT_TASK.get(), **entry})
        task = CURRENT_TASK.get()
        for path in entry.get("outside") or []:
            if (task or run_id, path) in self.noticed:
                continue
            self.noticed.add((task or run_id, path))
            note = f"{entry.get('agent') or entry.get('cli') or 'An agent'} read {path}"
            S.feed.push("Permission", {"run": run_id, "task": task, "agent": entry.get("agent"), "path": path, "text": note})
            S.store.message("skipper", f"ℹ️ {note} (read-only, outside the approved folders).", task or None)

    def finish(self, run_id: str, status: str, output: str, cli: str = "") -> None:
        row = S.store.run_finish(run_id, status, output, cli)
        if row:
            S.feed.push("Run", row)


IGNORED = ("token", "state.sqlite3", "state.sqlite3-*", "*.log", "ledger.json", "uploads/", "scout/", "work/", "mcp/")


def protect_home(home: Path) -> None:
    """~/.fam may be a git repo; keep secrets, state, logs and clones out of it (the .git is left alone)."""
    path = home / ".gitignore"
    have = path.read_text().splitlines() if path.exists() else []
    missing = [x for x in IGNORED if x not in have]
    if missing:
        path.write_text("\n".join([*have, *(["# added by Fam: never commit secrets, state or logs"] if not have else []), *missing]) + "\n")


def write_token(home: Path) -> str:
    token = secrets.token_urlsafe(32)
    path = home / "token"
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(token)
    return token


def grant(raw: str) -> Path:
    """Approve a folder the user named, after safety checks, and announce it."""
    path = safe_folder(raw)
    if S.store.grant_folder(str(path)):
        S.feed.push("FolderApproved", {"path": str(path), "implicit": True})
    return path


def spawn(coro, task_id=""):
    async def run():
        CURRENT_TASK.set(task_id)
        try:
            await coro
        except Exception as e:
            S.feed.push("Error", {"task": task_id, "message": f"{type(e).__name__}: {e}"[:500]})

    t = asyncio.create_task(run())
    S.jobs.add(t)
    t.add_done_callback(S.jobs.discard)
    return t


def repo_of(p):
    d = Path(p or os.environ.get("FAM_REPO") or ".").expanduser().resolve()
    if not (d / ".git").exists():
        raise HTTPException(400, f"{d} is not a git repository")
    if S.store and not S.skipper.authorized(d):
        try:
            return str(grant(str(d)))
        except ValueError as e:
            raise HTTPException(403, str(e)) from e
    return str(d)


@asynccontextmanager
async def life(_):
    home = Path(os.environ.get("FAM_HOME", Path.home() / ".fam")).expanduser()
    S.store = StateStore(home / "state.sqlite3")
    S.token = write_token(home)
    S.store.interrupt_runs()
    S.app = build(home, apply_accepted=True)
    initial = Path(os.environ.get("FAM_REPO") or Path(__file__).resolve().parent).expanduser().resolve()
    S.store.request_folder(str(initial))
    request = next((r for r in S.store.pending_folders() if r["path"] == str(initial)), None)
    if request:
        S.store.approve_folder(request["id"], str(initial))
    S.skipper = Skipper(S.store, S.app.skipper)
    S.app.profile.folders, S.app.profile.messages = S.store.approved_folders, S.store.messages
    for event in S.store.events(limit=100000):
        if event["type"] == "Outcome":
            data = event["data"]
            member = data["member"]
            S.app.lead.completed[member] += 1
            S.app.lead.reward[member] += float(data["reward"])
    S.feed = Feed(S.app.bus, S.store)
    Agent.tracker = RunTracker()
    Agent.ledger.attach(Path(S.store.path).parent / "ledger.json")
    protect_home(Path(S.store.path).parent)
    records = S.store.events(limit=100000)
    interrupted_research = {
        event["data"].get("task")
        for event in records
        if event["type"] == "Error"
        and event["data"].get("message", "").startswith("Research was interrupted by a Skipper restart.")
    }
    retried = {m.group(1) for e in records if e["type"] == "Error" for m in [re.search(r"restarted automatically as job (\w+)", e["data"].get("message", ""))] if m}
    for research in S.skipper.pending_research(records):
        if research["id"] in interrupted_research:
            continue
        if research["id"] in retried or not research.get("question"):  # retry once only
            S.feed.push("Error", {"task": research["id"], "message": "Research was interrupted by a Skipper restart again. Its partial results were not reported as complete; ask Quill to retry."})
            continue
        again = ResearchRequest(str(research["question"]), list(research.get("folders") or []))
        S.feed.push("Error", {"task": research["id"], "message": f"Research was interrupted by a Skipper restart; restarted automatically as job {again.id}."})
        spawn(S.app.bus.emit(again), again.id)
    interrupted = S.skipper.interrupted_tasks(records)
    notified = {
        event["data"]["task"]
        for event in records
        if event["type"] == "Error"
        and event["data"].get("message", "").startswith("Work was interrupted by a Skipper restart.")
    }
    for task_id in interrupted:
        if task_id not in notified:
            S.feed.push(
                "Error",
                {
                    "task": task_id,
                    "message": "Work was interrupted by a Skipper restart. Its isolated worktree was preserved; ask Skipper for help before resuming.",
                },
            )
    for stored in {**S.skipper.pending_local_apply(records), **{t["id"]: t for t in S.skipper.pending_apply(records)}}.values():
        task = Task(**stored)
        spawn(S.app.lead.apply(task), task.id)
    pending = S.skipper.pending_decisions(records)
    for stored in pending:
        S.app.steward.adopt(Task(**stored))
    if pending:
        ids = ", ".join(task["id"] for task in pending)
        notice = f"Task(s) {ids} are waiting for your decision."
        if not any(notice in message["text"] for message in S.store.messages(limit=100)):
            S.store.message("skipper", f"{notice} Send “accept task <id>” or “reject task <id>”.")
    S.every = 1800
    S.ticker = asyncio.create_task(Ticker(S.app.bus).run(S.every))
    yield
    if S.ticker:
        S.ticker.cancel()
        await asyncio.gather(S.ticker, return_exceptions=True)
    jobs = set(S.jobs)
    if jobs:
        _, pending = await asyncio.wait(jobs, timeout=20)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


api = FastAPI(title="Fam Console", lifespan=life)
api.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])


@api.middleware("http")
async def require_token(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith("/api/"):
        given = request.headers.get(TOKEN_HEADER, "")
        if not S.token or not hmac.compare_digest(given, S.token):
            return JSONResponse({"detail": "Missing or invalid Fam token."}, status_code=401)
    return await call_next(request)


class TaskIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    repo: str = ""
    pin: str = ""


class TossIn(BaseModel):
    to: str
    ask: str = Field(min_length=1, max_length=8000)
    repo: str = ""


class TextIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class WatchIn(BaseModel):
    every: float = Field(ge=1, le=86400)


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    repo: str = ""
    attachments: list[str] = Field(default_factory=list, max_length=10)


class FolderIn(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


class FolderApprovalIn(BaseModel):
    id: int = Field(gt=0)


@api.get("/")
def index():
    # Same-origin bootstrap: other sites cannot read this response, and TrustedHost blocks DNS rebinding.
    page = (WEB / "index.html").read_text().replace("__FAM_TOKEN__", S.token)
    return HTMLResponse(page, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"})


@api.get("/api/state")
def state():
    a, rules = S.app, next(iter(S.app.cast.values())).rules
    return {
        "repo": os.environ.get("FAM_REPO") or os.getcwd(),
        "budget": {"cap": Agent.ledger.cap, "spent": round(Agent.ledger.spent, 4)},
        "watch": S.every,
        "running": len(S.jobs),
        "crew": [{"key": k, "name": p.name, "model": MODEL.get(k, "copilot"), "mission": p.mission.split(". ")[0], "voice": p.voice, "builder": k in a.lead.members, "active": len(a.lead.active[k]), "available": not a.lead.active[k], "completed": a.lead.completed[k], "confidence": round(a.lead.reward[k] / a.lead.completed[k], 2) if a.lead.completed[k] else 0.5} for k, p in P.items()],
        "health": S.skipper.health(a),
        "proposals": a.linker.props,
        "trusted": {n: {**v, "ready": ready(v["cfg"]), "score": v["a"] / (v["a"] + v["b"])} for n, v in a.linker.trusted.items()},
        "queue": a.linker.queue,
        "dead": a.linker.dead,
        "rules": {"active": rules.active, "counts": dict(rules.n), "candidates": dict(rules.cand)},
    }


@api.get("/api/health")
def health():
    return {
        "pid": os.getpid(),
        "repo": os.environ.get("FAM_REPO") or os.getcwd(),
        **S.skipper.health(S.app),
    }


@api.get("/api/runs")
def runs(limit: int = 100):
    return S.store.runs(max(1, min(limit, 500)))


@api.get("/api/chat/history")
def chat_history():
    return S.store.messages()


def fam_home() -> Path:
    return Path(S.store.path).parent


@api.post("/api/upload")
async def upload(request: Request):
    """Raw-body upload (no multipart dependency): name in X-Filename. Token checked by middleware."""
    size = int(request.headers.get("content-length") or 0)
    if size > uploads.MAX_BYTES:
        raise HTTPException(413, f"The attachment is larger than {uploads.MAX_BYTES // 1024} KB.")
    data = b""
    async for chunk in request.stream():
        data += chunk
        if len(data) > uploads.MAX_BYTES:
            raise HTTPException(413, f"The attachment is larger than {uploads.MAX_BYTES // 1024} KB.")
    try:
        meta = uploads.save(fam_home(), urllib.parse.unquote(request.headers.get("x-filename", "")), data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    S.feed.push("Activity", {"source": "upload", "text": f"Received {meta['name']} ({meta['size']} bytes)", "level": ""})
    return {k: meta[k] for k in ("id", "name", "size", "type")}


@api.get("/api/uploads/{uid}")
async def get_upload(uid: str, request: Request):
    """Previews need the token too: attachments are private."""
    given = request.headers.get(TOKEN_HEADER, "")
    if not S.token or not hmac.compare_digest(given, S.token):
        raise HTTPException(401, "Missing or invalid Fam token.")
    meta = uploads.find(fam_home(), uid)
    if not meta:
        raise HTTPException(404, "No such attachment.")
    return FileResponse(meta["path"], media_type=meta["type"], headers={"X-Content-Type-Options": "nosniff", "Content-Disposition": f"inline; filename={meta['name']}"})


@api.post("/api/chat")
async def chat(b: ChatIn):
    try:
        named = folders_in(b.text)
        for extra in named[1:]:
            grant(extra)
        raw_path = (named[0] if named else "") or b.repo or os.environ.get("FAM_REPO") or "."
        directory = Path(raw_path).expanduser().resolve()
        if not S.skipper.authorized(directory):
            directory = grant(str(directory))
        repo = repo_of(str(directory)) if (directory / ".git").exists() else str(directory)
        def enqueue(event, task_id=""):
            work = event if asyncio.iscoroutine(event) else S.app.bus.emit(event)
            spawn(work, task_id or getattr(event, "id", ""))

        files = [f for uid in b.attachments if (f := uploads.find(fam_home(), uid))]
        if len(files) != len(b.attachments):
            raise ValueError("One of the attachments was not found; upload it again.")
        result = await S.skipper.chat(b.text + uploads.prompt_block(files), repo, S.app, enqueue)
        if isinstance(result, dict):
            result.setdefault("repo", repo)
        return result
    except HTTPException:
        raise
    except (ValueError, OSError) as e:
        raise HTTPException(400, str(e)[:500])
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {e}"[:500])


@api.get("/api/folders")
def folders():
    return {
        "approved": S.store.approved_folders(),
        "pending": S.store.pending_folders(),
    }


@api.post("/api/folders/request")
def request_folder(b: FolderIn):
    try:
        path = grant(b.path)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"path": str(path), "approved": True}


@api.post("/api/folders/approve")
def approve_folder(b: FolderApprovalIn):
    request = next((r for r in S.store.pending_folders() if r["id"] == b.id), None)
    if not request:
        raise HTTPException(404, "That folder request is no longer pending.")
    try:
        path = safe_folder(request["path"])
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    S.store.approve_folder(b.id, str(path))
    S.feed.push("FolderApproved", {"path": str(path)})
    return {"path": str(path), "approved": True}


@api.get("/api/folders/browse")
def browse_folder(path: str):
    directory = Path(path).expanduser().resolve()
    if not S.skipper.authorized(directory):
        raise HTTPException(403, "Approve this folder or one of its parents before browsing it.")
    if not directory.is_dir():
        raise HTTPException(400, "That path is not a directory.")
    try:
        entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold()))
    except OSError as e:
        raise HTTPException(403, f"Cannot read this folder: {e}") from e
    return {
        "path": str(directory),
        "entries": [
            {"name": item.name, "path": str(item), "directory": item.is_dir(), "git": (item / ".git").exists()}
            for item in entries
            if item.is_dir() and not item.is_symlink()
        ][:500],
    }


@api.post("/api/folders/select")
def select_folder(b: FolderIn):
    directory = Path(b.path).expanduser().resolve()
    if not S.skipper.authorized(directory):
        raise HTTPException(403, "Approve this folder before selecting it.")
    if not directory.is_dir():
        raise HTTPException(400, "That path is not a directory.")
    S.store.message("skipper", f"Project folder selected: {directory}")
    return {"path": str(directory)}


@api.get("/api/stream")
async def stream(request: Request):
    q: asyncio.Queue[dict] = asyncio.Queue()
    S.feed.waiters.add(q)
    raw_last = request.headers.get("last-event-id", "0")
    last_id = int(raw_last) if raw_last.isdigit() else 0
    if not last_id:
        last_id = S.feed.n

    def sse(ev):
        return f"id: {ev['id']}\ndata: {json.dumps(ev, default=str)}\n\n"

    async def gen():
        sent = last_id
        try:
            for ev in list(S.feed.log):
                if ev["id"] <= sent:
                    continue
                sent = ev["id"]
                yield sse(ev)
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), 15)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                if ev["id"] > sent:
                    sent = ev["id"]
                    yield sse(ev)
        finally:
            S.feed.waiters.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@api.post("/api/tasks", status_code=202)
async def add_task(b: TaskIn):
    if b.pin and b.pin not in S.app.lead.members:
        raise HTTPException(400, f"unknown member {b.pin}")
    t = Task(b.text, repo_of(b.repo), pin=b.pin)
    spawn(S.app.bus.emit(t), t.id)
    return {"id": t.id}


@api.post("/api/toss")
async def toss(b: TossIn):
    if b.to not in S.app.cast:
        raise HTTPException(400, f"unknown member {b.to}")
    t = Task(b.ask, repo_of(b.repo))
    try:
        text = await S.app.lead.toss(t, b.to, b.ask)
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {e}"[:500])
    S.feed.push("TossReply", {"task": t.id, "to": b.to, "ask": b.ask, "text": text})
    return {"id": t.id, "text": text}


@api.post("/api/brief")
async def brief(b: TextIn):
    try:
        return await S.app.expert.brief(Task(b.text))
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {e}"[:500])


@api.post("/api/research", status_code=202)
async def research(b: TextIn):
    try:
        folders = [str(grant(f)) for f in folders_in(b.text)]
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    request = ResearchRequest(b.text, folders)
    spawn(S.app.bus.emit(request), request.id)
    return {}


@api.post("/api/focus", status_code=202)
async def focus(b: TextIn):
    spawn(S.app.bus.emit(Focus(b.text)))
    return {}


@api.post("/api/fix")
async def fix(b: TextIn):
    await S.app.bus.emit(Correction(b.text))
    return {}


@api.post("/api/approve")
async def approve(b: TextIn):
    if b.text not in S.app.linker.props:
        raise HTTPException(404, f"no proposal named {b.text}")
    await S.app.bus.emit(Approve(b.text))
    return {}


@api.post("/api/tick", status_code=202)
async def tick():
    spawn(S.app.bus.emit(Tick()))
    return {}


@api.post("/api/watch")
async def watch(b: WatchIn):
    if S.ticker:
        S.ticker.cancel()
    S.ticker, S.every = asyncio.create_task(Ticker(S.app.bus).run(b.every)), b.every
    return {"every": b.every}


@api.delete("/api/watch")
async def unwatch():
    if S.ticker:
        S.ticker.cancel()
    S.ticker = S.every = None
    return {}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:api", host="127.0.0.1", port=int(os.environ.get("PORT", 8000)))
