"use client";

export default function Error({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <main className="state">
      <div role="alert">
        <h1>Something went wrong</h1>
        <p className="dim">{error.message}</p>
      </div>
      <button type="button" className="btn primary" onClick={reset}>Try again</button>
    </main>
  );
}
