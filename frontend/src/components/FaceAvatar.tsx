import type { CSSProperties } from "react";

type State = "idle" | "working" | "talking";

const SHAPES = {
  round: "M20 2a18 18 0 1 1 0 36a18 18 0 1 1 0-36z",
  squircle: "M12 2h16a10 10 0 0 1 10 10v16a10 10 0 0 1-10 10H12A10 10 0 0 1 2 28V12A10 10 0 0 1 12 2z",
  oval: "M20 1C30 1 35 9 35 22S30 39 20 39 5 35 5 22 10 1 20 1z",
  hex: "M20 2l16 9v18l-16 9-16-9V11z",
  drop: "M20 2C28 12 34 18 34 26a14 14 0 0 1-28 0C6 18 12 12 20 2z",
  tri: "M20 4L37 34H3z",
};

const PERSONAS: Record<string, { shape: keyof typeof SHAPES; fill: string; look: number; blink: number }> = {
  skipper: { shape: "squircle", fill: "var(--accent)", look: 6, blink: 4 },
  steward: { shape: "tri", fill: "var(--member)", look: 4, blink: 3 },
  quill: { shape: "oval", fill: "var(--ok)", look: 9, blink: 5 },
  sage: { shape: "round", fill: "var(--warn)", look: 7, blink: 3.5 },
  pip: { shape: "drop", fill: "var(--danger)", look: 5, blink: 6 },
  blue: { shape: "hex", fill: "var(--ink)", look: 8, blink: 4.5 },
};

const FALLBACK = Object.values(PERSONAS);

const CSS = `
.fa .look{animation:fa-look var(--look) ease-in-out infinite}
.fa .blink{transform-box:fill-box;transform-origin:center;animation:fa-blink var(--blink) linear infinite}
.fa ellipse{fill:var(--on-accent)}
.fa .mouth{fill:none;stroke:var(--on-accent);stroke-width:2.4;stroke-linecap:round}
.fa[data-state=working] .mouth,.fa[data-state=talking] .mouth{transform-box:fill-box;transform-origin:center;animation:fa-talk 1.2s ease-in-out infinite}
.fa[data-state=talking] .mouth{animation-duration:.4s}
@keyframes fa-look{0%,100%{transform:translateX(0)}25%{transform:translateX(-1.5px)}75%{transform:translateX(1.5px)}}
@keyframes fa-blink{0%,92%,100%{transform:scaleY(1)}95%{transform:scaleY(.1)}}
@keyframes fa-talk{0%,100%{transform:scaleY(.25)}50%{transform:scaleY(1)}}
@media (prefers-reduced-motion:reduce){.fa *{animation:none!important}}
`;

export default function FaceAvatar({ id, name, state, size = 36 }: { id: string; name: string; state: State; size?: number }) {
  const p = PERSONAS[id] ?? FALLBACK[[...id].reduce((n, c) => n + c.charCodeAt(0), 0) % FALLBACK.length];
  return (
    <svg
      className="fa"
      data-member={id}
      data-state={state}
      viewBox="0 0 40 40"
      width={size}
      height={size}
      aria-hidden="true"
      style={{ "--look": `${p.look}s`, "--blink": `${p.blink}s`, flex: "none" } as CSSProperties}
    >
      <title>{name}</title>
      <style>{CSS}</style>
      <path d={SHAPES[p.shape]} fill={p.fill} stroke={p.fill} strokeWidth={3} strokeLinejoin="round" />
      <g className="look">
        <g className="blink">
          <ellipse cx="14" cy="18" rx="2.6" ry="3.2" />
          <ellipse cx="26" cy="18" rx="2.6" ry="3.2" />
        </g>
      </g>
      <path className="mouth" d="M14 27q6 5 12 0" />
    </svg>
  );
}
