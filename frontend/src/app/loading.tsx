export default function Loading() {
  return (
    <main className="state" aria-busy="true">
      <p role="status" className="dim"><span className="spinner" aria-hidden="true" /> Raising the crew…</p>
    </main>
  );
}
