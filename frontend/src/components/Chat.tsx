"use client";
import { useEffect, useRef, useState, type ClipboardEvent, type DragEvent, type FormEvent, type KeyboardEvent } from "react";
import FaceAvatar from "./FaceAvatar";
import { api, type Attachment } from "@/lib/api";
import type { Console } from "@/lib/useConsole";

function Preview({ file }: { file: Attachment }) {
  return (
    <span className="file">
      {file.blob && (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={file.blob} alt="" width={160} height={120} />
      )}
      {file.name} · {Math.max(1, Math.round(file.size / 1024))} KB
    </span>
  );
}

export default function Chat({ c }: { c: Console }) {
  const { lines, health, busy, error, setError, fail, send } = c;
  const [text, setText] = useState("");
  const [pending, setPending] = useState<Attachment[]>([]);
  const [drag, setDrag] = useState(false);
  const log = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);

  useEffect(() => {
    log.current?.scrollTo({ top: log.current.scrollHeight });
  }, [lines]);

  const attach = async (files: File[]) => {
    setError("");
    for (const file of files) {
      try {
        const meta = await api.upload(file);
        setPending((p) => [...p, meta]);
      } catch (e) {
        fail(new Error(`Could not attach ${file.name}: ${e instanceof Error ? e.message : e}`));
      }
    }
  };

  const submit = async (e?: FormEvent) => {
    e?.preventDefault();
    const body = text.trim();
    if (!body || busy) return;
    const files = pending;
    setPending([]);
    try {
      await send(body, files);
      setText("");
    } catch (err) {
      fail(err);
      setPending((p) => [...files, ...p]);
    }
    input.current?.focus();
  };

  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };
  const onPaste = (e: ClipboardEvent) => {
    const files = [...(e.clipboardData?.files ?? [])];
    if (files.length) {
      e.preventDefault();
      attach(files);
    }
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDrag(false);
    attach([...e.dataTransfer.files]);
  };

  const active = health?.running_tasks ?? 0;
  const who: Record<string, string> = { user: "You", skipper: "Skipper", member: "Crew" };

  return (
    <section className="center" id="log" aria-label="Log and conversation" tabIndex={-1}>
      <div className="health" role="status">
        {health && <span className="pill">{active ? `${active} task${active === 1 ? "" : "s"} in progress` : "Ready for your next task"}</span>}
      </div>
      <div className="messages" ref={log} role="log" aria-live="polite" aria-label="Messages" tabIndex={0}>
        {lines.length === 0 && (
          <div className="empty">
            <span className="orb" aria-hidden="true" />
            <strong>The log is empty.</strong>
            Tell Skipper what you want done. Work is assigned to the crew, and each step lands here.
          </div>
        )}
        <ol className="timeline">
          {lines.map((m) =>
            m.role === "event" ? (
              <li key={m.id} className="entry event">
                <p>{m.text}</p>
              </li>
            ) : (
              <li key={m.id} className={"entry " + m.role}>
                <div className="head">
                  {m.role !== "user" && <FaceAvatar id={m.role} state="neutral" size={22} />}
                  <b>{who[m.role] ?? m.role}</b>
                  {m.ts && <time dateTime={new Date(m.ts * 1000).toISOString()}>{new Date(m.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time>}
                </div>
                <div className="body">{m.text}</div>
                {!!m.files?.length && <div className="files">{m.files.map((f) => <Preview key={f.id} file={f} />)}</div>}
              </li>
            ),
          )}
        </ol>
      </div>
      <div className={drag ? "compose drag" : "compose"} onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)} onDrop={onDrop}>
        {busy && (
          <div className="typing" role="status">
            <span className="spinner" aria-hidden="true" />
            <span>Skipper is picking the right crew member…</span>
          </div>
        )}
        <div className="error" role="alert">{error}</div>
        <div className="chips">
          {pending.map((f) => (
            <span key={f.id} className="chip">
              <Preview file={f} />
              <button type="button" aria-label={"Remove " + f.name} onClick={() => setPending((p) => p.filter((x) => x.id !== f.id))}>×</button>
            </span>
          ))}
        </div>
        <form onSubmit={submit} aria-busy={busy}>
          <button type="button" className="btn attach" aria-label="Attach files" onClick={() => picker.current?.click()}>
            <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M21 11.5l-8.6 8.6a5 5 0 01-7-7l9-9a3.3 3.3 0 014.7 4.7l-9 9a1.7 1.7 0 01-2.4-2.4l8.2-8.2" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
          </button>
          <input ref={picker} type="file" multiple hidden onChange={(e) => { attach([...(e.target.files ?? [])]); e.target.value = ""; }} />
          <textarea ref={input} aria-label="Message to Skipper" aria-describedby="hint" placeholder="What outcome do you want?" maxLength={8000} required value={text} readOnly={busy} onChange={(e) => setText(e.target.value)} onKeyDown={onKey} onPaste={onPaste} />
          <button type="submit" className="btn primary" aria-disabled={busy || !text.trim() || undefined} data-busy={busy || undefined} onMouseDown={(e) => e.preventDefault()}>
            {busy ? <span className="spinner" aria-hidden="true" /> : <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>}
            Send
          </button>
        </form>
        <div id="hint" className="dim hint">Enter sends · Shift+Enter adds a line · attach, drop or paste files (10 MB) · type “status” for crew health</div>
      </div>
    </section>
  );
}
