import Link from "next/link";

export default function NotFound() {
  return (
    <main className="state">
      <h1>Off the chart</h1>
      <p className="dim">There is nothing at this address.</p>
      <Link className="btn" href="/">Back to the log</Link>
    </main>
  );
}
