"use client";
import { useState, type FormEvent, type ReactNode } from "react";
import { api, type Artifact, type Todo } from "@/lib/api";
import type { Console } from "@/lib/useConsole";

const BOX: Record<string, string> = { open: "☐", doing: "◐", waiting: "…", blocked: "!", done: "☑", stopped: "✕" };
const kb = (n: number) => Math.max(1, Math.round(n / 1024)) + " KB";

function Form({ label, button, onSubmit, children, fail }: { label: string; button: string; onSubmit: (v: string) => Promise<unknown>; children?: ReactNode; fail: (e: unknown) => void }) {
  const [v, setV] = useState("");
  const [done, setDone] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setDone("");
    onSubmit(v.trim()).then(() => { setV(""); setDone("Sent."); }).catch(fail);
  };
  return (
    <form className="inline" onSubmit={submit}>
      <label>{label}<input value={v} onChange={(e) => setV(e.target.value)} required maxLength={8000} /></label>
      {children}
      <button type="submit" className="btn">{button}</button>
      <span role="status" className="meta">{done}</span>
    </form>
  );
}

const Empty = ({ text }: { text: string }) => <p className="dim">{text}</p>;

export function Todos({ c }: { c: Console }) {
  const tops = c.todos.filter((i) => !i.parent);
  const finished = (i: Todo) => ["done", "stopped"].includes(i.status);
  const shown = [...tops.filter((i) => !finished(i)), ...tops.filter(finished).slice(-4)];
  if (!shown.length) return <Empty text="Nothing yet. Tasks and their steps appear here as they start." />;
  return (
    <ul className="feed" aria-live="polite">
      {shown.map((t) => (
        <li key={t.id} className={["blocked", "stopped"].includes(t.status) ? "warn" : ""}>
          <b>{BOX[t.status]} {t.text.slice(0, 90)}</b>
          <span className="meta">{t.owner || "unassigned"} · {t.task} · {t.status}{t.note ? ": " + t.note.slice(0, 100) : ""}</span>
          {c.todos.filter((s) => s.parent === t.id).map((s) => <span key={s.id} className="meta">{BOX[s.status]} {s.text}{s.note && s.status !== "done" ? " — " + s.note : ""}</span>)}
        </li>
      ))}
    </ul>
  );
}

export function Files({ c }: { c: Console }) {
  const get = (a: Artifact) => api.download(a).catch(c.fail);
  if (!c.artifacts.length) return <Empty text="Nothing yet. Ask for a spreadsheet, document or report." />;
  return (
    <ul className="feed" aria-live="polite">
      {c.artifacts.map((a) => (
        <li key={a.id}>
          <b>{a.name}</b>
          <span className="meta">{a.kind} · {kb(a.size)} · {a.by} · {new Date(a.ts * 1000).toLocaleTimeString()}</span>
          <button type="button" className="btn" onClick={() => get(a)}>Download {a.name}</button>
        </li>
      ))}
    </ul>
  );
}

export function Review({ c }: { c: Console }) {
  const say = (t: string) => c.send(t, []).catch(c.fail);
  const acts: Record<string, [string, string][]> = { decide: [["accept", "Accept"], ["reject", "Reject"]], apply: [["apply", "Apply"]], question: [] };
  return (
    <>
      {!c.review.length && <Empty text="Nothing needs you right now. Decisions, questions and accepted changes waiting to apply show up here." />}
      <ul className="feed" aria-live="polite">
        {c.review.map((r) => (
          <li key={r.kind + r.id} className="warn">
            <b>{{ decide: "Needs your decision", apply: "Ready to apply", question: "Question for you" }[r.kind]} · task {r.id}</b>
            <span className="meta">{r.text}</span>
            {acts[r.kind].map(([cmd, label]) => <button key={cmd} type="button" className="btn" onClick={() => say(`${cmd} ${r.id}`)}>{label} task {r.id}</button>)}
            {r.kind === "question" && <span className="meta">Answer in the chat.</span>}
          </li>
        ))}
      </ul>
      <p className="meta">Only items that arrive while this page is open are listed. After a reload, type “accept &lt;id&gt;” or “reject &lt;id&gt;” in the chat.</p>
    </>
  );
}

export function Habits({ c }: { c: Console }) {
  const say = (t: string) => c.send(t, []).catch(c.fail);
  const rules = c.state?.rules;
  return (
    <>
      <div className="inline">
        <button type="button" className="btn" onClick={() => say("habits")}>Show my habits</button>
        <button type="button" className="btn" onClick={() => say("learn from my work")}>Learn from my work</button>
      </div>
      <Form label="Teach Skipper a correction or standing rule" button="Teach" fail={c.fail} onSubmit={(t) => api.fix(t)} />
      <Form label="Forget habit number" button="Forget" fail={c.fail} onSubmit={(n) => say(`forget ${n}`)} />
      <h4 className="group">Learned rules</h4>
      {rules && rules.active.length ? <ul className="feed">{rules.active.map((r, i) => <li key={i}>{typeof r === "string" ? r : JSON.stringify(r)}</li>)}</ul> : <Empty text="No standing rules yet." />}
    </>
  );
}

export function Tools({ c }: { c: Console }) {
  const s = c.state;
  const props = Object.keys(s?.proposals ?? {});
  const trusted = Object.entries(s?.trusted ?? {});
  return (
    <>
      <h4 className="group">Proposed tools</h4>
      {props.length ? (
        <ul className="feed">
          {props.map((n) => (
            <li key={n}><b>{n}</b><button type="button" className="btn" onClick={() => api.approveTool(n).then(c.refreshSide).catch(c.fail)}>Approve {n}</button></li>
          ))}
        </ul>
      ) : <Empty text="No tools waiting for review." />}
      <h4 className="group">Trusted tools</h4>
      {trusted.length ? <ul className="feed">{trusted.map(([n, v]) => <li key={n}><b>{n}</b><span className="meta">{v.ready ? "ready" : "not ready"} · score {v.score.toFixed(2)}</span></li>)}</ul> : <Empty text="None yet." />}
      <p className="meta">Queued: {s?.queue.length ?? 0} · Dead: {s?.dead.length ?? 0}</p>
    </>
  );
}

export function Actions({ c }: { c: Console }) {
  const builders = c.health?.members.filter((m) => m.builder) ?? [];
  const members = c.health?.members ?? [];
  const [pin, setPin] = useState("");
  const [to, setTo] = useState("");
  const [reply, setReply] = useState("");
  const [brief, setBrief] = useState("");
  const [every, setEvery] = useState("60");
  const watching = !!c.state?.watch;
  return (
    <>
      <h4 className="group">New task</h4>
      <Form label="What should the family do?" button="Start task" fail={c.fail} onSubmit={(t) => api.task(t, c.repo.trim(), pin)}>
        <label>Pin to<select value={pin} onChange={(e) => setPin(e.target.value)}><option value="">Skipper picks</option>{builders.map((m) => <option key={m.key} value={m.key}>{m.name}</option>)}</select></label>
      </Form>
      <h4 className="group">Consult a member</h4>
      <Form label="What do you want to ask?" button="Ask" fail={c.fail} onSubmit={(a) => api.toss(to || members[0]?.key, a, c.repo.trim()).then((r) => setReply(r.text))}>
        <label>Ask<select value={to} onChange={(e) => setTo(e.target.value)}>{members.map((m) => <option key={m.key} value={m.key}>{m.name}</option>)}</select></label>
      </Form>
      {reply && <p className="feed" role="status"><span>{reply}</span></p>}
      <h4 className="group">Research</h4>
      <Form label="Question for Quill" button="Start research" fail={c.fail} onSubmit={api.research} />
      <h4 className="group">Focus</h4>
      <Form label="Field to focus on" button="Set focus" fail={c.fail} onSubmit={api.focus} />
      <h4 className="group">Brief</h4>
      <Form label="Topic for a brief" button="Get brief" fail={c.fail} onSubmit={(t) => api.brief(t).then((b) => setBrief(JSON.stringify(b, null, 2)))} />
      {brief && <pre className="meta">{brief}</pre>}
      <h4 className="group">Watch ticker</h4>
      <div className="inline">
        <label>Every (seconds)<input type="number" min={1} max={86400} value={every} onChange={(e) => setEvery(e.target.value)} /></label>
        <button type="button" className="btn" aria-pressed={watching} onClick={() => api.watch(watching ? null : Number(every)).then(c.refreshSide).catch(c.fail)}>Watch ticker</button>
        <button type="button" className="btn" onClick={() => api.tick().catch(c.fail)}>Tick now</button>
      </div>
      <p className="meta">{watching ? `Ticking every ${c.state?.watch}s.` : "Ticker is off."}</p>
    </>
  );
}
