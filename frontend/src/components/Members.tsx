import type { Member } from "@/lib/api";
import { sortMembers } from "@/lib/agents";
import type { Console } from "@/lib/useConsole";

function MemberCard({ m }: { m: Member }) {
  const task = m.active_tasks?.join(", ") || "assigned task";
  return (
    <li className={m.available ? "member" : "member busy"}>
      <span className="glyph" aria-hidden="true">{m.name[0]}</span>
      <div>
        <strong>{m.name}</strong>
        <p>{m.role}</p>
        <span className="availability" title={m.available ? undefined : task}>{m.available ? "Available" : `Working: ${task}`}</span>
      </div>
    </li>
  );
}

const money = (n: number) => (n > 0 && n < 0.01 ? "<$0.01" : "$" + n.toFixed(2));

export default function Crew({ c }: { c: Console }) {
  const { health, folders } = c;
  const jobs = (health?.research ?? []).filter((j) => j.status === "running");
  const sorted = sortMembers(health?.members ?? []);
  const working = sorted.filter((m) => !m.available);
  const idle = sorted.filter((m) => m.available);
  const spent = health?.budget.spent ?? 0;
  const cap = health?.budget.cap ?? 0;
  const pct = cap > 0 ? Math.min(100, Math.round((spent / cap) * 100)) : 0;

  return (
    <section className="crew" aria-labelledby="crew-h">
      <h2 id="crew-h">Crew</h2>
      {!health && <p className="dim">Waiting for the crew to report in…</p>}
      {health && (
        <>
          <ul className="members">
            {working.map((m) => <MemberCard key={m.name} m={m} />)}
            {jobs.map((j) => (
              <li key={j.id} className="member busy">
                <span className="glyph" aria-hidden="true">Q</span>
                <div>
                  <strong>Quill · research {j.id}</strong>
                  <p>{j.question}</p>
                  <span className="availability">In progress</span>
                </div>
              </li>
            ))}
            {idle.map((m) => <MemberCard key={m.name} m={m} />)}
          </ul>
          <div className="budget">
            <div className="budget-row"><span>Spend today</span><b>{money(spent)} / {money(cap)}</b></div>
            <div className="gauge" aria-hidden="true"><i style={{ width: pct + "%" }} /></div>
            <span className="sr">{pct}% of the daily budget used</span>
          </div>
          {folders.approved.length > 0 && (
            <details className="approved">
              <summary>{folders.approved.length} approved folder{folders.approved.length === 1 ? "" : "s"}</summary>
              <ul>{folders.approved.map((p) => <li key={p}>{p}</li>)}</ul>
            </details>
          )}
        </>
      )}
    </section>
  );
}
