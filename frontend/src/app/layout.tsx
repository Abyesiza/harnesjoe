import type { Metadata, Viewport } from "next";
import { Fraunces, Geist_Mono, Space_Grotesk } from "next/font/google";
import "./globals.css";

const display = Fraunces({ subsets: ["latin"], axes: ["opsz"], variable: "--font-display", display: "swap" });
const sans = Space_Grotesk({ subsets: ["latin"], variable: "--font-sans", display: "swap" });
const mono = Geist_Mono({ subsets: ["latin"], variable: "--font-mono", display: "swap" });

export const metadata: Metadata = {
  title: { default: "Skipper · ship’s log", template: "%s · Skipper" },
  description: "Chat with Skipper, watch the crew work, and review every run in one log.",
  referrer: "no-referrer",
};
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  colorScheme: "light dark",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#9AA597" },
    { media: "(prefers-color-scheme: dark)", color: "#1B211A" },
  ],
};

const theme = `try{var t=localStorage.getItem("fam-theme");document.documentElement.dataset.theme=t==="light"||t==="dark"?t:matchMedia("(prefers-color-scheme:dark)").matches?"dark":"light"}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${display.variable} ${sans.variable} ${mono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: theme }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
