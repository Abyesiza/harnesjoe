"use client";
import { useState } from "react";
import { useConsole } from "@/lib/useConsole";
import Chat from "./Chat";
import Crew from "./Members";
import FolderDialog from "./FolderDialog";
import Header from "./Header";
import Panels from "./Panels";
import w from "./Watch.module.css";
import { useCollapsed } from "@/lib/useCollapsed";

const VIEWS = [
  ["log", "Log"],
  ["crew", "Crew"],
  ["watch", "Watch"],
] as const;

export default function Console() {
  const c = useConsole();
  const [collapsed, toggleWatch] = useCollapsed();
  const [folders, setFolders] = useState(false);
  const [view, setView] = useState<(typeof VIEWS)[number][0]>("log");

  return (
    <>
      <a className="skip-link" href="#log">Skip to the log</a>
      <Header connection={c.connection} repo={c.repo} setRepo={c.setRepo} onFolders={() => setFolders(true)} onStatus={() => c.send("status", []).catch(c.fail)} />
      <main className={w.main} data-view={view} data-watch={collapsed ? "closed" : "open"}>
        <Crew c={c} />
        <Chat c={c} />
        <Panels c={c} collapsed={collapsed} onToggle={toggleWatch} />
      </main>
      <nav className="tabbar" aria-label="Views">
        {VIEWS.map(([id, label]) => (
          <button key={id} type="button" aria-pressed={view === id} onClick={() => setView(id)}>{label}</button>
        ))}
      </nav>
      <FolderDialog open={folders} onClose={() => setFolders(false)} folders={c.folders} refreshFolders={c.refreshFolders} setRepo={c.setRepo} fail={c.fail} />
    </>
  );
}
