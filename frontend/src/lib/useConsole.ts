"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useCallback, useEffect, useState } from "react";
import { api, type Attachment, type ChatMessage, type Folders, type Health, type Run, type Source } from "./api";
import { ACTIVITY_TEXT, REFRESH, words } from "./events";

export type Item = { id: number; title: string; text?: string; meta?: string; cls?: string; sources?: Source[] };
export type Line = ChatMessage & { id: number };

const ago = (ts?: number) => (ts ? new Date(ts * 1000).toLocaleTimeString() : "");
let seq = 0;
const line = (m: ChatMessage): Line => ({ ...m, id: ++seq });
const item = (i: Omit<Item, "id">): Item => ({ ...i, id: ++seq });
const role = (r: string) => (r === "user" || r === "member" ? r : "skipper");

export function useConsole() {
  const [connection, setConnection] = useState("connecting");
  const [error, setError] = useState("");
  const [health, setHealth] = useState<Health | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [research, setResearch] = useState<Item[]>([]);
  const [activity, setActivity] = useState<Item[]>([]);
  const [folders, setFolders] = useState<Folders>({ pending: [], approved: [] });
  const [repo, setRepo] = useState("");
  const [busy, setBusy] = useState(false);
  const fail = useCallback((e: unknown) => setError(e instanceof Error ? e.message : String(e)), []);

  const say = useCallback((m: ChatMessage) => setLines((l) => [...l, line(m)]), []);
  const addRun = useCallback((r: Run) => setRuns((l) => [r, ...l.filter((x) => x.id !== r.id)].slice(0, 80)), []);
  const addResearch = useCallback((i: Omit<Item, "id">) => setResearch((l) => [item(i), ...l].slice(0, 80)), []);
  const addActivity = useCallback((i: Omit<Item, "id">) => setActivity((l) => [item(i), ...l].slice(0, 80)), []);

  const refreshHealth = useCallback(
    () =>
      api
        .health()
        .then((h) => {
          setHealth(h);
          setConnection("connected");
          setRepo((r) => r || h.repo || "");
        })
        .catch((e) => {
          setConnection("reconnecting");
          fail(e);
        }),
    [fail],
  );

  const refreshFolders = useCallback(() => api.folders().then(setFolders).catch(fail), [fail]);

  useEffect(() => {
    api.history().then((h) => setLines(h.map((m) => line({ ...m, role: role(m.role) })))).catch(fail);
    api.runs().then((r) => setRuns(r)).catch(fail);
    refreshFolders();
    refreshHealth();
    const timer = setInterval(refreshHealth, 5000);
    return () => clearInterval(timer);
  }, [fail, refreshHealth, refreshFolders]);

  useEffect(() => {
    const stream = new EventSource("/api/stream");
    stream.onopen = () => {
      setConnection("connected");
      setError("");
    };
    stream.onerror = () => setConnection("reconnecting");
    stream.onmessage = async (e) => {
      try {
        const event = JSON.parse(e.data);
        const d: any = event.data;
        const type: string = event.type;
        const text = words[type]?.(d);
        const when = ago(event.ts);
        const sources = (title: string, list: Source[]) => {
          const served = [...new Set(list.map((s) => s.provider).filter(Boolean))];
          addResearch({ title, meta: (served.length ? "via " + served.join(", ") + " · " : "") + when, sources: list, cls: list.length ? "" : "warn" });
        };
        if (type === "Run") addRun(d);
        if (type === "Command") api.runs().then((rows) => rows.filter((r) => r.id === d.run).forEach(addRun)).catch(() => {});
        if (type === "Fallback") addActivity({ title: "CLI fallback", text: `${d.failed} → ${d.to}: ${d.reason}`, meta: when, cls: "warn" });
        if (type === "Searched") sources(`Search: ${d.query}`, d.sources || []);
        if (type === "Finding" && (d.sources || []).length) sources(`Finding ${d.id}: ${d.question || ""}`, d.sources);
        if (type === "Activity") addActivity({ title: d.source, text: d.text, meta: when, cls: d.level });
        if (ACTIVITY_TEXT.includes(type) && text) addActivity({ title: type, text, meta: when });
        if (type === "Permission") {
          addActivity({ title: "Permissions", text: d.text, meta: when });
          setLines((l) => [...l, line({ role: "event", text: `ℹ️ ${d.text} (read-only)`, ts: event.ts })]);
        }
        if (text) setLines((l) => [...l, line({ role: "event", text, ts: event.ts })]);
        if (REFRESH.includes(type)) await refreshHealth();
        if (type === "FolderApproved" || type === "FolderApprovalNeeded") await refreshFolders();
      } catch (err) {
        fail(err);
      }
    };
    return () => stream.close();
  }, [addActivity, addResearch, addRun, fail, refreshFolders, refreshHealth]);

  const send = useCallback(
    async (text: string, files: Attachment[]) => {
      setError("");
      setBusy(true);
      say({ role: "user", text, files });
      try {
        const result = await api.chat(text, repo.trim(), files.map((f) => f.id));
        result.messages.filter((m) => m.role !== "user").forEach((m) => say({ ...m, role: role(m.role) }));
        await refreshHealth();
      } finally {
        setBusy(false);
      }
    },
    [repo, refreshHealth, say],
  );

  return { connection, error, setError, fail, health, lines, say, runs, research, activity, folders, refreshFolders, repo, setRepo, send, busy };
}

export type Console = ReturnType<typeof useConsole>;
