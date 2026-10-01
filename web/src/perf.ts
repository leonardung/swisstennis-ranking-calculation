/** Page loading times, sent to /api/perf (see the /perf report). A page view is timed from the
 * navigation (the first one: from the start of the page load) until its data requests are done:
 * the requests in flight have all ended and no other started for QUIET ms. Views left before
 * that, failed ones, or ones in a hidden tab are not sent. */

export type PerfKind = "initial" | "navigation";

const QUIET = 50;
const MAX_MS = 60_000;

let inflight = 0;
let current: { page: string; kind: PerfKind; t0: number; end: number; timer?: number } | null = null;

/** Wraps a network request (cached responses do not count). */
export async function timedRequest<T>(p: Promise<T>): Promise<T> {
  inflight++;
  if (current) window.clearTimeout(current.timer);
  try {
    return await p;
  } catch (e) {
    // a failed page (backend loading, offline) is not a loading time
    if ((e as Error)?.name !== "AbortError" && current) {
      window.clearTimeout(current.timer);
      current = null;
    }
    throw e;
  } finally {
    inflight--;
    settle();
  }
}

/** The page `page` is being shown since t0 (performance.now(), 0 for the page load). */
export function startPageTiming(page: string, kind: PerfKind, t0: number): void {
  if (current) window.clearTimeout(current.timer);
  current = document.visibilityState === "visible" ? { page, kind, t0, end: performance.now() } : null;
  settle();
}

function settle(): void {
  const c = current;
  if (!c || inflight > 0) return;
  c.end = performance.now();
  window.clearTimeout(c.timer);
  c.timer = window.setTimeout(() => {
    if (current !== c || inflight > 0) return;
    current = null;
    send(c.kind, c.page, c.end - c.t0);
  }, QUIET);
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden" && current) {
    window.clearTimeout(current.timer);
    current = null;
  }
});

function send(kind: PerfKind, page: string, ms: number): void {
  if (!(ms >= 0 && ms <= MAX_MS)) return;
  try {
    void fetch("/api/perf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, page, ms: Math.round(ms) }),
      keepalive: true,
    }).catch(() => {});
  } catch {
    /* timing is best effort */
  }
}

export interface PerfRow {
  kind: "initial" | "navigation" | "api";
  page: string;
  n: number;
  avg: number;
  p50: number;
  p95: number;
  p99: number;
}

/** The report (PERF_TOKEN on the server); throws the HTTP status on failure. */
export async function perfSummary(token: string, days: number): Promise<PerfRow[]> {
  const res = await fetch(`/api/perf?days=${days}`, { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) throw res.status;
  return ((await res.json()) as { rows: PerfRow[] }).rows;
}
