export type Member = { key: string; name: string; role: string; available: boolean; builder: boolean; active_tasks?: string[] };
export type Health = {
  repo?: string;
  running_tasks: number;
  budget: { spent: number; cap: number };
  members: Member[];
  research?: { id: string; status: string; question: string }[];
};
export type Attachment = { id: string; name: string; type: string; size: number; blob?: string };
export type ChatMessage = { role: string; text: string; ts?: number; files?: Attachment[] };
export type Source = { url?: string; title?: string; snippet?: string; provider?: string };
export type Run = {
  id: string;
  task?: string;
  cli: string;
  cwd: string;
  prompt: string;
  status: string;
  started?: number;
  ended?: number;
  output?: string;
  fallbacks?: { failed: string }[];
  commands?: { command: string; status: string; output?: string; exit?: number | null; seconds?: number; outside?: string[] }[];
};
export type Todo = { id: number; text: string; owner: string; task: string; parent: number; status: string; note: string };
export type Artifact = { id: string; name: string; kind: string; size: number; by: string; task: string; ts: number };
export type State = {
  watch: number | null;
  proposals: Record<string, unknown>;
  trusted: Record<string, { ready: boolean; score: number }>;
  queue: unknown[];
  dead: unknown[];
  rules: { active: unknown[]; counts: Record<string, number>; candidates: Record<string, unknown> };
  crew: { key: string; name: string; model: string; mission: string; builder: boolean; completed: number; confidence: number }[];
};
export type Folders = { pending: { id: number; path: string }[]; approved: string[] };
export type Browse = { path: string; entries: { name: string; path: string; git?: boolean }[] };

let token: Promise<string> | undefined;
const getToken = () => (token ??= fetch("/token").then((r) => r.json()).then((j) => j.token as string));

async function send<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.method && init.method !== "GET") headers.set("X-Fam-Token", await getToken());
  const response = await fetch(path, { ...init, headers });
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || response.statusText);
  return body;
}

const post = <T>(path: string, data: unknown) =>
  send<T>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

export const api = {
  health: () => send<Health>("/api/health"),
  history: () => send<ChatMessage[]>("/api/chat/history"),
  runs: () => send<Run[]>("/api/runs?limit=50"),
  state: () => send<State>("/api/state"),
  todos: () => send<{ items: Todo[] }>("/api/todos").then((r) => r.items),
  artifacts: () => send<{ items: Artifact[] }>("/api/artifacts").then((r) => r.items.reverse()),
  async download(a: Artifact) {
    const response = await fetch(`/api/artifacts/${a.id}/file`, { headers: { "X-Fam-Token": await getToken() } });
    if (!response.ok) throw new Error((await response.json()).detail || response.statusText);
    const link = document.createElement("a");
    link.href = URL.createObjectURL(await response.blob());
    link.download = a.name;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 10000);
  },
  task: (text: string, repo: string, pin: string) => post("/api/tasks", { text, repo, pin }),
  toss: (to: string, ask: string, repo: string) => post<{ text: string }>("/api/toss", { to, ask, repo }),
  research: (text: string) => post("/api/research", { text }),
  focus: (text: string) => post("/api/focus", { text }),
  fix: (text: string) => post("/api/fix", { text }),
  approveTool: (text: string) => post("/api/approve", { text }),
  brief: (text: string) => post<Record<string, unknown>>("/api/brief", { text }),
  tick: () => post("/api/tick", {}),
  watch: (every: number | null) => (every ? post("/api/watch", { every }) : send("/api/watch", { method: "DELETE" })),
  folders: () => send<Folders>("/api/folders"),
  browse: (path: string) => send<Browse>("/api/folders/browse?path=" + encodeURIComponent(path)),
  chat: (text: string, repo: string, attachments: string[]) => post<{ messages: ChatMessage[] }>("/api/chat", { text, repo, attachments }),
  requestFolder: (path: string) => post("/api/folders/request", { path }),
  approveFolder: (id: number) => post("/api/folders/approve", { id }),
  selectFolder: (path: string) => post("/api/folders/select", { path }),
  async upload(file: File): Promise<Attachment> {
    const meta = await send<Attachment>("/api/upload", {
      method: "POST",
      headers: { "X-Filename": encodeURIComponent(file.name || "pasted.png"), "Content-Type": "application/octet-stream" },
      body: file,
    });
    return meta.type.startsWith("image/") ? { ...meta, blob: URL.createObjectURL(file) } : meta;
  },
};
