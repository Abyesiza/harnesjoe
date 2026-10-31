import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent
HOME = Path(os.environ.get("FAM_HOME", Path.home() / ".fam")).expanduser()
PID = HOME / "daemon.pid"
LOG = HOME / "daemon.log"
TOKEN = HOME / "token"
BASE = f"http://127.0.0.1:{os.environ.get('PORT', '8000')}"
# Loopback only: a corporate HTTP_PROXY would otherwise answer for 127.0.0.1 (403).
urlopen = build_opener(ProxyHandler({})).open


def request(path, method="GET", data=None):
    body = json.dumps(data).encode() if data is not None else None
    headers = {"Content-Type": "application/json"}
    if TOKEN.exists():
        headers["X-Fam-Token"] = TOKEN.read_text().strip()
    req = Request(BASE + path, data=body, method=method, headers=headers)
    with urlopen(req, timeout=30) as response:
        return json.loads(response.read())


def alive(pid):
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    # os.kill(pid, 0) terminates the process on Windows, so query its exit code instead.
    import ctypes

    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = ctypes.c_ulong()
    k32.GetExitCodeProcess(handle, ctypes.byref(code))
    k32.CloseHandle(handle)
    return code.value == 259  # STILL_ACTIVE


def running():
    try:
        pid = int(PID.read_text().strip())
    except (OSError, ValueError):
        return None
    if alive(pid):
        try:
            if request("/api/health").get("pid") != pid:
                raise ValueError("another process answers on the port")
        except OSError:
            return pid  # alive but not answering (hung or still starting): never spawn a duplicate
        except ValueError:
            pass
        else:
            return pid
    PID.unlink(missing_ok=True)
    return None


def serve():
    import uvicorn

    from server import api

    uvicorn.run(api, host="127.0.0.1", port=int(os.environ.get("PORT", 8000)), log_level="info", timeout_graceful_shutdown=5)  # open SSE streams never end on their own


def start():
    HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
    pid = running()
    if pid:
        print(f"Skipper is already running (PID {pid}) at {BASE}")
        return
    try:
        health = request("/api/health")
    except OSError:
        pass
    else:
        print(f"Skipper is already running (PID {health['pid']}) at {BASE}")
        return
    LOG.touch(mode=0o600, exist_ok=True)
    LOG.chmod(0o600)
    with LOG.open("a") as log:
        log.seek(0, os.SEEK_END)
        log_offset = log.tell()
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "serve"],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    PID.write_text(str(process.pid))
    PID.chmod(0o600)
    for _ in range(60):
        try:
            health = request("/api/health")
        except OSError:
            if process.poll() is not None:
                if PID.exists() and PID.read_text().strip() == str(process.pid):
                    PID.unlink()
                with LOG.open("r") as log:
                    log.seek(log_offset)
                    details = log.read().strip()
                if not details:
                    details = "The server process exited before the health check succeeded."
                raise RuntimeError(f"Skipper failed to start:\n{details}\nFull log: {LOG}")
            time.sleep(0.25)
            continue
        if health["pid"] != process.pid:
            process.terminate()
            process.wait(timeout=5)
            PID.unlink(missing_ok=True)
            raise RuntimeError(f"Port {BASE} is serving another Skipper process.")
        print(f"Skipper is running at {BASE}")
        return
    print(f"Skipper is starting (PID {process.pid}). See {LOG} for details.")


def service_active() -> bool:
    try:
        done = subprocess.run(["systemctl", "--user", "is-active", "--quiet", "fam.service"], check=False)
    except FileNotFoundError:
        return False
    return done.returncode == 0


def wait_healthy(old_pid=None, timeout: float = 180.0) -> dict:
    """Wait until a new server answers /api/health and the auth token file exists."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            health = request("/api/health")
        except OSError:
            health = None
        if health and health.get("pid") != old_pid and TOKEN.exists():
            return health
        time.sleep(1)
    raise RuntimeError(f"Skipper did not become healthy within {int(timeout)}s. See `journalctl --user -u fam.service` or {LOG}.")


def restart():
    """Restart Skipper: through systemd when the service is installed, otherwise stop then start."""
    try:
        old = request("/api/health").get("pid")
    except OSError:
        old = None
    began = time.monotonic()
    if service_active():
        subprocess.run(["systemctl", "--user", "restart", "fam.service"], check=True)
        health = wait_healthy(old)
    else:
        stop()
        start()
        health = wait_healthy(None)
    print(f"Skipper restarted (PID {health.get('pid')}) at {BASE} in {time.monotonic() - began:.0f}s.")


def up():
    start()
    if not webbrowser.open(BASE):
        print(f"Open {BASE} in your browser to use Skipper.")


def stop():
    pid = running()
    if not pid:
        print("Skipper is not running.")
        return
    os.kill(pid, signal.SIGTERM)
    for _ in range(40):
        try:
            health = request("/api/health")
        except OSError:
            PID.unlink(missing_ok=True)
            print("Skipper stopped.")
            return
        if health.get("pid") != pid:
            PID.unlink(missing_ok=True)
            print("Skipper stopped.")
            return
        time.sleep(0.1)
    raise RuntimeError(f"Skipper did not stop cleanly; inspect PID {pid} and {LOG}.")


def status():
    try:
        h = request("/api/health")
    except OSError as e:
        print(f"Skipper is not responding: {e}")
        return
    print(f"Skipper is running at {BASE}; {h['running_tasks']} active task(s).")
    for member in h["members"]:
        availability = f"working on {member['active']} task(s)" if not member["available"] else "available"
        print(f"- {member['name']}: {availability}; {member['completed_tasks']} completed, {member['confidence']:.0%} average")
    print(f"Budget: ${h['budget']['spent']:.2f} / ${h['budget']['cap']:.2f}")
    print("Recent CLI runs:")
    runs(5)


def runs(limit: int = 20) -> None:
    try:
        rows = request(f"/api/runs?limit={limit}")
    except OSError as e:
        print(f"Skipper is not responding: {e}")
        return
    if not rows:
        print("No CLI runs recorded yet.")
    for r in rows:
        hops = " -> ".join([*(f["failed"] for f in r["fallbacks"]), r["cli"]])
        task = f" task {r['task']}" if r["task"] else ""
        print(f"{r['id']} {r['status']:11} {hops:18}{task} in {r['cwd']}: {r['prompt'][:80]}")


def upload(path):
    """Send one file to /api/upload; returns its attachment id."""
    from urllib.parse import quote as q
    file = Path(path).expanduser()
    if not file.is_file():
        raise SystemExit(f"Not a file: {file}")
    headers = {"Content-Type": "application/octet-stream", "X-Filename": q(file.name)}
    if TOKEN.exists():
        headers["X-Fam-Token"] = TOKEN.read_text().strip()
    with urlopen(Request(BASE + "/api/upload", data=file.read_bytes(), method="POST", headers=headers), timeout=60) as response:
        return json.loads(response.read())["id"]


def chat(text, attach=()):
    try:
        ids = [upload(f) for f in attach]
        result = request("/api/chat", "POST", {"text": text, "repo": os.getcwd(), "attachments": ids})
        print(result["reply"])
    except HTTPError as e:
        detail = e.read().decode(errors="replace")
        print(f"Skipper could not complete that request: {detail}", file=sys.stderr)
        raise SystemExit(1) from e
    except URLError as e:
        print(f"Skipper is not running. Start it with: python daemon.py start ({e})", file=sys.stderr)
        raise SystemExit(1) from e


def install_service():
    unit = Path.home() / ".config/systemd/user/fam.service"
    unit.parent.mkdir(parents=True, exist_ok=True)

    def quote(value):
        return '"' + str(value).replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"') + '"'

    def unit_path(value):
        return str(value).replace("%", "%%").replace("\\", "\\x5c").replace(" ", "\\x20")

    cli_dirs = [
        str(Path(found).resolve().parent)
        for name in ("claude", "copilot")
        if (found := shutil.which(name))
    ]
    service_path = os.pathsep.join(dict.fromkeys([*cli_dirs, os.environ.get("PATH", "")]))
    unit.write_text(
        "[Unit]\nDescription=Fam Skipper local assistant\nAfter=network.target\n\n"
        "[Service]\nType=simple\nTimeoutStopSec=40\n"
        f"WorkingDirectory={unit_path(ROOT)}\n"
        f"ExecStart={quote(sys.executable)} {quote(Path(__file__).resolve())} serve\n"
        f"Environment={quote('FAM_HOME=' + str(HOME))}\n"
        f"Environment={quote('PORT=' + os.environ.get('PORT', '8000'))}\n"
        f"Environment={quote('PATH=' + service_path)}\n"
        "Restart=on-failure\nRestartSec=3\n"
        "Environment=PYTHONUNBUFFERED=1\n\n"
        "[Install]\nWantedBy=default.target\n"
    )
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "fam.service"], check=True)
    active = subprocess.run(["systemctl", "--user", "is-active", "--quiet", "fam.service"], check=False)
    if active.returncode:
        details = subprocess.run(
            ["systemctl", "--user", "status", "fam.service", "--no-pager"],
            capture_output=True,
            text=True,
            check=False,
        )
        raise RuntimeError(
            "fam.service did not become active:\n"
            + (details.stdout or details.stderr).strip()
        )
    print(f"Installed and started {unit}")


def service_status():
    subprocess.run(["systemctl", "--user", "status", "fam.service", "--no-pager"], check=False)


def service_stop():
    subprocess.run(["systemctl", "--user", "disable", "--now", "fam.service"], check=False)


def main():
    parser = argparse.ArgumentParser(description="Talk to and manage your local Skipper daemon.")
    sub = parser.add_subparsers(dest="command", required=True)
    helps = {
        "up": "start and open the dashboard",
        "start": "start in the background",
        "stop": "stop the background server",
        "restart": "restart (systemd if fam.service is active, else stop+start); waits for health and token",
        "status": "show health",
        "runs": "list recent CLI runs",
        "serve": "run the server in the foreground",
        "install-service": "install the systemd user service",
        "service-status": "show systemd service status",
        "service-stop": "disable and stop the systemd service",
    }
    for name, text in helps.items():
        sub.add_parser(name, help=text)
    ask = sub.add_parser("chat")
    ask.add_argument("text", nargs="+")
    ask.add_argument("--attach", action="append", default=[], metavar="FILE", help="attach a file (repeatable)")
    sub.add_parser("talk")
    args = parser.parse_args()
    if args.command == "talk":
        print("Talk to Skipper. Type /quit to leave.")
        while True:
            try:
                text = input("You: ").strip()
            except EOFError:
                break
            if text.lower() in {"/quit", "/exit"}:
                break
            if text:
                print("Skipper: ", end="", flush=True)
                chat(text)
        return
    {
        "up": up,
        "start": start,
        "stop": stop,
        "restart": restart,
        "status": status,
        "runs": runs,
        "serve": serve,
        "install-service": install_service,
        "service-status": service_status,
        "service-stop": service_stop,
    }.get(args.command, lambda: chat(" ".join(args.text), args.attach))()


if __name__ == "__main__":
    main()
