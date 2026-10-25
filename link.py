import asyncio
import json
import os
import re
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

from core import Activity, Approve, Briefed, Node, Outcome, Proposal, Tick, on
from mind import enc

SEEDS = ("filesystem", "github", "fetch", "sqlite", "playwright")
RUN = {
    "npm": lambda p, v: ("npx", ["-y", f"{p['identifier']}@{p.get('version', v)}"]),
    "pypi": lambda p, v: ("uvx", [f"{p['identifier']}@{p.get('version', v)}"]),
    "oci": lambda p, v: ("docker", ["run", "-i", "--rm", p["identifier"]]),
}


def registry(q):
    u = "https://registry.modelcontextprotocol.io/v0.1/servers?" + urllib.parse.urlencode({"search": q, "version": "latest", "limit": 20})
    return [x["server"] for x in json.load(urllib.request.urlopen(u, timeout=20))["servers"]]


def config(sv):
    for p in sv.get("packages", []):
        if p.get("registryType") in RUN and p.get("transport", {}).get("type") == "stdio":
            cmd, args = RUN[p["registryType"]](p, sv.get("version", "latest"))
            args += [a["value"] for a in p.get("packageArguments", []) if a.get("type") == "positional" and a.get("value")]
            env = {e["name"]: e.get("value") or "${" + e["name"] + "}" for e in p.get("environmentVariables", [])}
            return {"type": "stdio", "command": cmd, "args": args, **({"env": env} if env else {})}
    for r in sv.get("remotes", []):
        if r.get("type") == "streamable-http" and not any(h.get("isRequired") for h in r.get("headers", [])):
            return {"type": "http", "url": r["url"]}
    return None


def ready(cfg):
    return all(os.environ.get(m) for v in cfg.get("env", {}).values() for m in re.findall(r"\$\{(\w+)\}", v))


class Linker(Node):
    def __init__(s, bus, root, find=registry, auto=False, keep=2, retire=0.3, floor=0.3):
        super().__init__(bus)
        s.root, s.find, s.auto, s.keep, s.retire, s.floor = Path(root), find, auto, keep, retire, floor
        s.path, s.used = s.root / "mcp.json", {}
        d = json.loads(s.path.read_text()) if s.path.exists() else {"queue": list(SEEDS)}
        s.trusted, s.props, s.queue, s.dead = d.get("trusted", {}), d.get("props", {}), d.get("queue", []), d.get("dead", [])

    def save(s):
        s.root.mkdir(parents=True, exist_ok=True)
        s.path.write_text(json.dumps({"trusted": s.trusted, "props": s.props, "queue": s.queue, "dead": s.dead}))

    def write(s, names):
        key = re.sub(r"\W+", "-", "+".join(sorted(names))) or "none"
        f = s.root / "mcp" / f"{key[:60]}-{zlib.crc32(key.encode()):08x}.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"mcpServers": {n.split("/")[-1]: s.trusted[n]["cfg"] for n in sorted(names)}}))
        return str(f)

    def score(s, need, name, desc, mean=0.5):
        return float(enc(need) @ enc(f"{name} {desc}")) + 0.5 * mean

    def always(s):
        return [n for n, v in s.trusted.items() if v.get("always") and ready(v["cfg"])]

    def add(s, name, cfg, desc="", always=False):
        s.trusted[name] = {"cfg": cfg, "desc": desc, "a": 1.0, "b": 1.0, "always": always}
        s.props.pop(name, None)
        s.save()

    def forget(s, name):
        s.trusted.pop(name, None)
        s.props.pop(name, None)
        s.save()

    def match(s, need):
        x = enc(need)
        ok = [(s.score(need, n, v["desc"], v["a"] / (v["a"] + v["b"])), n) for n, v in s.trusted.items() if ready(v["cfg"]) and x @ enc(f"{n} {v['desc']}") >= s.floor]
        return [n for _, n in sorted(ok, reverse=True)[: s.keep]]

    async def search(s, need):
        terms = list(dict.fromkeys(re.findall(r"[a-z0-9]{3,}", need.lower())))[:4]
        pools = await asyncio.gather(*(asyncio.to_thread(s.find, t) for t in terms), return_exceptions=True)
        hits = {sv["name"]: sv for p in pools if not isinstance(p, Exception) for sv in p if config(sv) and sv["name"] not in s.dead and sv["name"] not in s.trusted}
        return sorted(hits.values(), key=lambda sv: -s.score(need, sv["name"], sv.get("description", "")))

    async def propose(s, need, hits):
        for sv in hits[:3]:
            s.props[sv["name"]] = {"cfg": config(sv), "desc": sv.get("description", ""), "need": need}
        s.save()
        await s.bus.emit(Proposal(need, [sv["name"] for sv in hits[:3]]))

    def trust(s, name):
        p = s.props.pop(name, None)
        if p:
            s.trusted[name] = {"cfg": p["cfg"], "desc": p["desc"], "a": 1.0, "b": 1.0}
            s.save()
        return p

    async def fulfil(s, need, tid):
        names = s.match(need)
        if not names:
            hits = await s.search(need)
            if hits:
                await s.propose(need, hits)
                if s.auto and s.trust(hits[0]["name"]):
                    names = s.match(need)
        if names:
            s.used[tid] = names
            return s.write(names)
        return None

    @on(Approve)
    async def approve(s, a):
        s.trust(a.name)

    @on(Briefed)
    async def hint(s, b):
        if not b.cached:
            s.queue += [r for r in b.brief.get("rare", []) if r not in s.queue]
            s.save()

    @on(Tick)
    async def explore(s, _):
        if s.queue:
            need = s.queue.pop(0)
            await s.bus.emit(Activity("mcp", f"Searching MCP registry for: {need}"))
            hits = await s.search(need)
            await s.bus.emit(Activity("mcp", f"Found {len(hits)} MCP server(s) for: {need}" if hits else f"No MCP servers matched: {need}"))
            if hits:
                await s.propose(need, hits)
            s.save()

    @on(Outcome)
    async def credit(s, o):
        for n in s.used.pop(o.task.id, []):
            v = s.trusted.get(n)
            if v:
                v["a"] += o.reward
                v["b"] += 1 - o.reward
                if v["a"] + v["b"] >= 7 and v["a"] / (v["a"] + v["b"]) < s.retire:
                    s.dead.append(n)
                    del s.trusted[n]
        s.save()
