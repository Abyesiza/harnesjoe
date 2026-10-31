import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const css = readFileSync(fileURLToPath(new URL("../src/app/globals.css", import.meta.url)), "utf8");
const block = (sel) => css.slice(css.indexOf(sel + "{") + sel.length + 1, css.indexOf("}", css.indexOf(sel + "{")));
const tokens = (sel) => Object.fromEntries([...block(sel).matchAll(/--([\w-]+):(#[0-9a-f]{6})/gi)].map(([, k, v]) => [k, v]));

const lum = (hex) => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const ratio = (a, b) => {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

const TEXT = [
  ["ink", ["bg", "surface", "raised", "raised-hover", "raised-active"]],
  ["muted", ["bg", "surface", "raised", "raised-hover"]],
  ["accent", ["bg", "surface", "raised"]],
  ["on-accent", ["accent", "accent-hover"]],
  ["on-header", ["header"]],
  ["ok", ["surface", "raised"]],
  ["warn", ["surface", "raised"]],
  ["danger", ["surface", "raised"]],
  ["member", ["surface", "raised"]],
];
const NON_TEXT = [
  ["line-strong", ["surface", "raised"]],
  ["focus", ["bg", "surface", "raised"]],
];

let failed = 0;
for (const theme of ["light", "dark"]) {
  const vars = { ...tokens(":root"), ...(theme === "dark" ? tokens(":root[data-theme=dark]") : {}) };
  const check = (fg, bg, min, kind) => {
    const r = ratio(vars[fg], vars[bg]);
    const ok = r >= min;
    if (!ok) failed++;
    console.log(`${theme.padEnd(5)} ${kind.padEnd(8)} ${fg.padEnd(12)} on ${bg.padEnd(12)} ${r.toFixed(2)}:1 need ${min} ${ok ? "PASS" : "FAIL"}`);
  };
  for (const [fg, bgs] of TEXT) for (const bg of bgs) check(fg, bg, 4.5, "text");
  for (const [fg, bgs] of NON_TEXT) for (const bg of bgs) check(fg, bg, 3, "non-text");
}

if (failed) {
  console.error(`${failed} contrast check(s) failed`);
  process.exit(1);
}
console.log("All contrast checks passed");
