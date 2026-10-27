import { useCallback, useSyncExternalStore } from "react";

const KEY = "fam-watch";
const listeners = new Set<() => void>();

const subscribe = (f: () => void) => {
  listeners.add(f);
  window.addEventListener("storage", f);
  return () => {
    listeners.delete(f);
    window.removeEventListener("storage", f);
  };
};

const read = () => {
  try {
    return localStorage.getItem(KEY) === "closed";
  } catch {
    return false;
  }
};

export function useCollapsed(): [boolean, () => void] {
  const collapsed = useSyncExternalStore(subscribe, read, () => false);
  const toggle = useCallback(() => {
    try {
      localStorage.setItem(KEY, read() ? "open" : "closed");
    } catch {}
    listeners.forEach((f) => f());
  }, []);
  return [collapsed, toggle];
}
