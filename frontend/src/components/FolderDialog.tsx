"use client";
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { api, type Browse } from "@/lib/api";
import type { Console } from "@/lib/useConsole";

type Props = Pick<Console, "folders" | "refreshFolders" | "setRepo" | "fail"> & { open: boolean; onClose: () => void };

export default function FolderDialog({ open, onClose, folders, refreshFolders, setRepo, fail }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [path, setPath] = useState("");
  const [browse, setBrowse] = useState<Browse | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const d = dialog.current;
    if (!d) return;
    if (open && !d.open) {
      d.showModal();
      refreshFolders();
    }
    if (!open && d.open) d.close();
  }, [open, refreshFolders]);

  const trap = (e: KeyboardEvent) => {
    if (e.key !== "Tab") return;
    const all = [...dialog.current!.querySelectorAll<HTMLElement>("a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex]:not([tabindex='-1'])")];
    const edge = e.shiftKey ? all[0] : all[all.length - 1];
    if (document.activeElement !== edge) return;
    e.preventDefault();
    (e.shiftKey ? all[all.length - 1] : all[0]).focus();
  };

  const run = (job: () => Promise<unknown>) => job().catch(fail);

  const request = (e: FormEvent) => {
    e.preventDefault();
    run(async () => {
      await api.requestFolder(path.trim());
      setPath("");
      await refreshFolders();
    });
  };

  const open_ = (p: string) =>
    run(async () => {
      setLoading(true);
      try {
        setBrowse(await api.browse(p));
      } finally {
        setLoading(false);
      }
    });

  const use = (p: string) =>
    run(async () => {
      setRepo(p);
      await api.selectFolder(p);
      onClose();
    });

  return (
    <dialog ref={dialog} aria-labelledby="folder-title" onClose={onClose} onKeyDown={trap} onClick={(e) => e.target === dialog.current && onClose()}>
      <div className="dialog">
        <div className="dialog-head">
          <h2 id="folder-title" className="title">Project folders and access</h2>
          <span className="grow" />
          <button type="button" onClick={onClose}>Close</button>
        </div>
        <p>Name a project folder here or in chat and Skipper can use it immediately. Access covers that folder and its subfolders only; system, home-root and credential folders are refused.</p>
        <form className="folder-form" onSubmit={request}>
          <input aria-label="Absolute folder path" placeholder="/home/you/Projects" required value={path} onChange={(e) => setPath(e.target.value)} />
          <button type="submit">Use folder</button>
        </form>
        {folders.pending.map((f) => (
          <div key={f.id} className="folder-row">
            <span>Waiting for your approval: {f.path}</span>
            <button type="button" onClick={() => run(async () => { await api.approveFolder(f.id); await refreshFolders(); })}>Approve access</button>
          </div>
        ))}
        <h2 className="label">Approved folders</h2>
        {folders.approved.map((p) => (
          <div key={p} className="folder-row">
            <span>{p}</span>
            <button type="button" onClick={() => open_(p)}>Browse</button>
          </div>
        ))}
        {!folders.approved.length && <p className="dim">No approved folders yet. Request one above, then approve it to browse here.</p>}
        <h2 className="label">Browse approved folder</h2>
        <div className="dim" aria-live="polite">{browse?.path ?? "Choose an approved folder above."}</div>
        {loading && <p className="dim" role="status">Loading folder…</p>}
        <div className="browse">
          {browse?.entries.map((en) => (
            <div key={en.path} className="folder-row">
              <span>{en.name}{en.git ? " · Git repository" : ""}</span>
              <button type="button" onClick={() => (en.git ? use(en.path) : open_(en.path))}>{en.git ? "Use project" : "Open"}</button>
            </div>
          ))}
          {browse && !browse.entries.length && "No subfolders found."}
        </div>
      </div>
    </dialog>
  );
}
