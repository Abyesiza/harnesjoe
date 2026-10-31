"use client";
import { useState, type KeyboardEvent, type ReactNode } from "react";
import type { Run, Source } from "@/lib/api";
import type { Console, Item } from "@/lib/useConsole";
import { Actions, Files, Habits, Review, Todos, Tools } from "./Extras";
import s from "./Watch.module.css";

const TABS = [
  ["todo", "To do"],
  ["review", "Review"],
  ["files", "Files"],
  ["runs", "CLI runs"],
  ["research", "Research"],
  ["habits", "Habits"],
  ["tools", "Tools"],
  ["activity", "Self-improve & MCP"],
  ["actions", "Actions"],
] as const;

const ago = (ts?: number) => (ts ? new Date(ts * 1000).toLocaleTimeString() : "");

function Link({ s }: { s: Source }) {
  const url = s.url ?? "";
  let safe = false;
  try {
    safe = ["http:", "https:"].includes(new URL(url).protocol);
  } catch {}
  return safe ? <a href={url} target="_blank" rel="noopener noreferrer">{s.title || url}</a> : <span>{url}</span>;
}

const ICON: Record<string, string> = { running: "●", done: "✓", failed: "✕", interrupted: "✕", blocked: "⛔", queued: "◷" };

function RunItem({ r }: { r: Run }) {
  const hops = [...(r.fallbacks ?? []).map((f) => f.failed), r.cli].join(" → ");
  const reads = [...new Set((r.commands ?? []).flatMap((c) => c.outside ?? []))];
  const cmds = (r.commands ?? []).filter((c) => c.status !== "read");
  return (
    <li className={s.run} tabIndex={0}>
      <div className={s.top}>
        <span className={s.hops}>{hops}</span>
        <span className={`${s.chip} ${s[r.status] ?? ""}`}><span aria-hidden="true">{ICON[r.status] ?? "•"}</span>{r.status}</span>
      </div>
      <div className={s.prompt}>{r.prompt}</div>
      <span className={s.meta}>
        {r.id}{r.task ? ` · task ${r.task}` : ""} · {r.started && <time dateTime={new Date(r.started * 1000).toISOString()}>{ago(r.started)}</time>}{r.ended ? "–" + ago(r.ended) : ""} · {r.cwd}
      </span>
      {cmds.length > 0 && (
        <ul className={s.cmds} aria-label="Commands">
          {cmds.map((c, i) => {
            const bad = c.status === "blocked" || c.status === "failed";
            const state = c.status === "blocked" ? "blocked: " + c.output : c.exit == null ? "running" : `exit ${c.exit}${c.seconds != null ? " · " + c.seconds + "s" : ""}`;
            return (
              <li key={i} className={bad ? s.bad : undefined} title={c.output && c.status !== "blocked" ? c.output.slice(-1000) : undefined}>
                <span>$ {c.command}</span>
                <span>{state}</span>
              </li>
            );
          })}
        </ul>
      )}
      {r.output && (
        <details>
          <summary>Output</summary>
          <pre>{r.output.slice(-240)}</pre>
        </details>
      )}
      {reads.length > 0 && (
        <details>
          <summary>Permissions ({reads.length})</summary>
          <pre>read {reads.join(", ")}</pre>
        </details>
      )}
    </li>
  );
}

function Empty({ title, hint }: { title: string; hint: string }) {
  return (
    <div className={s.empty}>
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M8 12h8" /></svg>
      <strong>{title}</strong>
      <span>{hint}</span>
    </div>
  );
}

function RunList({ runs }: { runs: Run[] }) {
  if (!runs.length) return <Empty title="No CLI runs yet" hint="Ask Skipper to start a task and the run shows up here." />;
  const sorted = [...runs].sort((a, b) => (b.started ?? 0) - (a.started ?? 0));
  const live = sorted.filter((r) => r.status === "running");
  const rest = sorted.filter((r) => r.status !== "running");
  return (
    <>
      {[["Running", live], ["Recent", rest]].map(([label, list]) => (list as Run[]).length > 0 && (
        <div key={label as string}>
          <h4 className={s.group}>{label as string}</h4>
          <ul className={s.runs}>{(list as Run[]).map((r) => <RunItem key={r.id} r={r} />)}</ul>
        </div>
      ))}
    </>
  );
}

function Feed({ items, empty }: { items: Item[]; empty: [string, string] }) {
  return (
    items.length === 0 ? <Empty title={empty[0]} hint={empty[1]} /> : <ul className="feed">
      {items.map((i) => (
        <li key={i.id} className={i.cls}>
          <b>{i.title} </b>
          {i.text && <span>{i.text}</span>}
          {i.meta && <span className="meta">{i.meta}</span>}
          {i.sources && !i.sources.length && <div>No web sources returned.</div>}
          {i.sources?.map((s, n) => (
            <div key={n}>
              <Link s={s} />
              {s.snippet && <span className="meta">{s.snippet}</span>}
            </div>
          ))}
        </li>
      ))}
    </ul>
  );
}

function Jobs({ c }: { c: Console }) {
  const jobs = c.health?.research ?? [];
  return jobs.length > 0 && <ul className="feed" aria-label="Research jobs">{jobs.map((j) => <li key={j.id}><b>{j.id} · {j.status}</b><span className="meta">{j.question}</span></li>)}</ul>;
}

export default function Panels({ c, collapsed, onToggle }: { c: Console; collapsed: boolean; onToggle: () => void }) {
  const [tab, setTab] = useState<(typeof TABS)[number][0]>("todo");

  const onKey = (e: KeyboardEvent, i: number) => {
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!step && e.key !== "Home" && e.key !== "End") return;
    e.preventDefault();
    const next = e.key === "Home" ? 0 : e.key === "End" ? TABS.length - 1 : (i + step + TABS.length) % TABS.length;
    setTab(TABS[next][0]);
    document.getElementById("t-" + TABS[next][0])?.focus();
  };

  const live = c.runs.filter((r) => r.status === "running").length;
  const counts: Record<string, number> = { todo: c.todos.filter((t) => !t.parent && !["done", "stopped"].includes(t.status)).length, review: c.review.length, files: c.artifacts.length, tools: Object.keys(c.state?.proposals ?? {}).length, runs: c.runs.length, research: c.research.length, activity: c.activity.length };

  const panels: Record<string, [string, ReactNode]> = {
    todo: ["To do: tasks and steps", <Todos key="t" c={c} />],
    review: ["Review: decisions, questions, apply", <Review key="v" c={c} />],
    files: ["Files and artifacts", <Files key="f" c={c} />],
    habits: ["Habits and teaching", <Habits key="h" c={c} />],
    tools: ["Tool proposals", <Tools key="l" c={c} />],
    actions: ["Start work and consult", <Actions key="x" c={c} />],
    runs: ["Copilot / Claude runs", <RunList key="r" runs={c.runs} />],
    research: ["Web research & sources", <><Jobs key="j" c={c} /><Feed key="s" items={c.research} empty={["No research yet", "Ask Quill a question."]} /></>],
    activity: ["Daemon, scout & MCP", <Feed key="a" items={c.activity} empty={["Quiet for now", "Fallbacks, scouting and MCP events land here."]} />],
  };

  return (
    <section className={`watch ${s.panel} ${collapsed ? s.collapsed : ""}`} aria-labelledby="watch-h">
      <div className={s.head}>
        <h2 id="watch-h">Watch</h2>
        {collapsed && live > 0 && <span className={s.badge} aria-hidden="true">{live}</span>}
        <button type="button" className={`btn ${s.toggle}`} aria-expanded={!collapsed} aria-controls="watch-body" aria-label="Watch panel" title={collapsed ? "Expand Watch panel" : "Collapse Watch panel"} onClick={onToggle}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M6 3l5 5-5 5" /></svg>
        </button>
      </div>
      <p className="sr" role="status">{live} CLI run{live === 1 ? "" : "s"} running</p>
      <div id="watch-body" className={s.body}>
      <div className="tabs" role="tablist" aria-label="Live activity">
        {TABS.map(([id, label], i) => (
          <button key={id} type="button" role="tab" id={"t-" + id} aria-selected={tab === id} aria-controls={"tab-" + id} tabIndex={tab === id ? 0 : -1} onClick={() => setTab(id)} onKeyDown={(e) => onKey(e, i)}>
            {label}{counts[id] > 0 && <span className={s["tab-count"]}>{counts[id]}</span>}
          </button>
        ))}
      </div>
      {TABS.map(([id]) => (
        <div key={id} id={"tab-" + id} role="tabpanel" aria-labelledby={"t-" + id} tabIndex={0} className={s.scroller} hidden={tab !== id}>
          <h3 className={s.sectionHead}>{panels[id][0]}</h3>
          {panels[id][1]}
        </div>
      ))}
      </div>
    </section>
  );
}
