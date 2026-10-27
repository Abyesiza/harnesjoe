"use client";
import { useSyncExternalStore } from "react";
import type { Console } from "@/lib/useConsole";
import styles from "./Header.module.css";

type Props = Pick<Console, "connection" | "repo" | "setRepo"> & { onFolders: () => void; onStatus: () => void };

const root = () => document.documentElement;
const subscribe = (notify: () => void) => {
  const watch = new MutationObserver(notify);
  watch.observe(root(), { attributes: true, attributeFilter: ["data-theme"] });
  return () => watch.disconnect();
};
const isDark = () => root().dataset.theme === "dark";

export default function Header({ connection, repo, setRepo, onFolders, onStatus }: Props) {
  const dark = useSyncExternalStore(subscribe, isDark, () => false);

  const toggle = () => {
    const next = dark ? "light" : "dark";
    root().dataset.theme = next;
    try {
      localStorage.setItem("fam-theme", next);
    } catch {}
  };

  return (
    <header className={styles.bar}>
      <h1 className={styles.brand}>
        <svg viewBox="0 0 32 32" width="28" height="28" aria-hidden="true"><path d="M16 5v17M16 5l8 12h-8M8 24h16l-3 3H11z" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" /></svg>
        Skipper
      </h1>
      <span role="status" className={styles.connection} data-state={connection}>{connection === "connected" ? "Connected" : connection === "reconnecting" ? "Reconnecting…" : "Connecting…"}</span>
      <span className={styles.grow} />
      <input className={styles.repo} aria-label="Selected project folder" title="Selected project folder" placeholder="Project folder" value={repo} onChange={(e) => setRepo(e.target.value)} />
      <button type="button" className={styles.action} onClick={onFolders}>Folders &amp; access</button>
      <button type="button" className={styles.action} onClick={onStatus}>Status</button>
      <button type="button" className={styles.action} aria-pressed={dark} onClick={toggle}>Dark mode</button>
    </header>
  );
}
