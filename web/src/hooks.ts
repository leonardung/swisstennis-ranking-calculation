import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

export interface AsyncState<T> {
  data: T | null;
  error: ApiError | Error | null;
  loading: boolean;
  /** True while the backend answers 503 (still loading its data); retries automatically. */
  backendLoading: boolean;
  reload: () => void;
}

const RETRY_MS = 4000;

/**
 * Runs an abortable fetcher whenever `deps` change. A 503 from the backend
 * ("loading") is retried every few seconds until it succeeds.
 */
export function useAsync<T>(
  fetcher: ((signal: AbortSignal) => Promise<T>) | null,
  deps: unknown[],
): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [loading, setLoading] = useState<boolean>(fetcher != null);
  const [backendLoading, setBackendLoading] = useState(false);
  const [nonce, setNonce] = useState(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    const f = fetcherRef.current;
    if (!f) {
      setData(null);
      setError(null);
      setLoading(false);
      return;
    }
    const ctrl = new AbortController();
    let timer: number | undefined;
    let cancelled = false;
    setLoading(true);
    setError(null);

    const run = () => {
      f(ctrl.signal).then(
        (d) => {
          if (cancelled) return;
          setData(d);
          setBackendLoading(false);
          setLoading(false);
        },
        (e: unknown) => {
          if (cancelled || (e as Error)?.name === "AbortError") return;
          if (e instanceof ApiError && e.loading) {
            setBackendLoading(true);
            timer = window.setTimeout(run, RETRY_MS);
            return;
          }
          setBackendLoading(false);
          setError(e as Error);
          setData(null);
          setLoading(false);
        },
      );
    };
    run();
    return () => {
      cancelled = true;
      ctrl.abort();
      if (timer) window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { data, error, loading, backendLoading, reload };
}

export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);
  return v;
}

export type Tab = "overview" | "matches" | "h2h" | "simulation" | "stats" | "tournaments";
export const TABS: Tab[] = ["overview", "matches", "h2h", "simulation", "stats", "tournaments"];
/** tabs renamed since: old links still open the right one */
const OLD_TABS: Record<string, Tab> = { simulator: "h2h" };

export type ClubTab = "players" | "tournaments";
export const CLUB_TABS: ClubTab[] = ["players", "tournaments"];

export type Route =
  | { page: "home" }
  | { page: "player"; id: number; tab: Tab }
  | { page: "tournaments" }
  | { page: "tournament"; id: number; eventId: number | null }
  | { page: "clubs" }
  | { page: "club"; id: number; tab: ClubTab }
  | { page: "perf" }
  | { page: "login" }
  | { page: "account" }
  | { page: "privacy" };

export function parsePath(path: string): Route {
  const m = /^\/player\/(\d+)(?:\/([a-z0-9]+))?\/?$/.exec(path);
  if (m) {
    const name = OLD_TABS[m[2] ?? ""] ?? m[2] ?? "";
    const tab = (TABS as string[]).includes(name) ? (name as Tab) : "overview";
    return { page: "player", id: Number(m[1]), tab };
  }
  if (/^\/tournaments\/?$/.test(path)) return { page: "tournaments" };
  if (/^\/perf\/?$/.test(path)) return { page: "perf" };
  if (/^\/clubs\/?$/.test(path)) return { page: "clubs" };
  if (/^\/login\/?$/.test(path)) return { page: "login" };
  if (/^\/account\/?$/.test(path)) return { page: "account" };
  if (/^\/privacy\/?$/.test(path)) return { page: "privacy" };
  const c = /^\/club\/(\d+)(?:\/(tournaments))?\/?$/.exec(path);
  if (c) return { page: "club", id: Number(c[1]), tab: c[2] ? "tournaments" : "players" };
  const t = /^\/tournament\/(\d+)(?:\/(\d+))?\/?$/.exec(path);
  if (t) return { page: "tournament", id: Number(t[1]), eventId: t[2] ? Number(t[2]) : null };
  return { page: "home" };
}

export function playerHref(id: number, tab?: Tab): string {
  return tab && tab !== "overview" ? `/player/${id}/${tab}` : `/player/${id}`;
}

export function clubHref(id: number, tab?: ClubTab): string {
  return tab === "tournaments" ? `/club/${id}/tournaments` : `/club/${id}`;
}

export function tournamentHref(id: number, eventId?: number | null): string {
  return eventId != null ? `/tournament/${id}/${eventId}` : `/tournament/${id}`;
}

const ROUTE_CHANGE = "routechange";

export function navigate(href: string): void {
  if (window.location.pathname + window.location.search === href) return;
  window.history.pushState({ entry: newEntry() }, "", href);
  window.dispatchEvent(new Event(ROUTE_CHANGE));
  window.scrollTo({ top: 0 });
}

/** A value kept in the query of the URL (?key=value): a refresh or going back finds it again.
 * A change replaces the history entry (going back does not step through the filters). */
export function useUrlState(key: string, initial: string): [string, (v: string) => void] {
  const [value, setValue] = useState(() => new URLSearchParams(window.location.search).get(key) ?? initial);
  const set = useCallback(
    (v: string) => {
      setValue(v);
      const url = new URL(window.location.href);
      if (v === initial) url.searchParams.delete(key);
      else url.searchParams.set(key, v);
      window.history.replaceState(window.history.state, "", url);
    },
    [key, initial],
  );
  return [value, set];
}

// Scroll position per history entry (session storage), set again after a refresh or going back
// once the page is tall enough: the content arrives asynchronously, which the browser's own
// restoration does not wait for.
const SCROLL_KEY = "scroll:";
const RESTORE_MS = 5000;
let restoring = false;

function newEntry(): string {
  return Math.random().toString(36).slice(2);
}

function entryId(): string {
  let id = window.history.state?.entry as string | undefined;
  if (!id) {
    id = newEntry();
    window.history.replaceState({ ...window.history.state, entry: id }, "");
  }
  return id;
}

function saveScroll(): void {
  // a modal pins the page (see showModal): its offset is not a scroll position
  if (restoring || document.body.style.position === "fixed") return;
  try {
    sessionStorage.setItem(SCROLL_KEY + entryId(), String(Math.round(window.scrollY)));
  } catch {
    /* storage unavailable */
  }
}

function restoreScroll(): void {
  let target = 0;
  try {
    target = Number(sessionStorage.getItem(SCROLL_KEY + entryId())) || 0;
  } catch {
    /* storage unavailable */
  }
  if (target <= 0) return;
  restoring = true;
  const until = performance.now() + RESTORE_MS;
  const inputs = ["wheel", "touchstart", "keydown", "mousedown"];
  const stop = () => {
    restoring = false;
    inputs.forEach((e) => window.removeEventListener(e, stop));
  };
  inputs.forEach((e) => window.addEventListener(e, stop, { passive: true }));
  const step = () => {
    if (!restoring) return;
    const reachable = document.documentElement.scrollHeight - window.innerHeight >= target;
    window.scrollTo({ top: target, behavior: "instant" });
    if (reachable || performance.now() > until) stop();
    else requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function keepScroll(): void {
  window.history.scrollRestoration = "manual";
  let queued = false;
  window.addEventListener(
    "scroll",
    () => {
      if (queued) return;
      queued = true;
      requestAnimationFrame(() => {
        queued = false;
        saveScroll();
      });
    },
    { passive: true },
  );
  window.addEventListener("popstate", restoreScroll);
  restoreScroll();
}

/** Links shared before the switch from hash routes (#/player/1) keep working. */
function redirectHashRoute(): void {
  if (window.location.hash.startsWith("#/")) {
    window.history.replaceState(null, "", window.location.hash.slice(1));
  }
}
redirectHashRoute();
keepScroll();

/** The current route; plain same-site links navigate without reloading the page. */
export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parsePath(window.location.pathname));
  useEffect(() => {
    const on = () => {
      redirectHashRoute();
      setRoute(parsePath(window.location.pathname));
    };
    const click = (e: MouseEvent) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const a = (e.target as Element).closest?.("a");
      if (!a || a.target || a.hasAttribute("download") || a.origin !== window.location.origin) return;
      // the server's own routes (Google sign-in redirects): a real page load
      if (a.pathname.startsWith("/api/")) return;
      e.preventDefault();
      navigate(a.pathname + a.search);
    };
    window.addEventListener("popstate", on);
    window.addEventListener("hashchange", on);
    window.addEventListener(ROUTE_CHANGE, on);
    document.addEventListener("click", click);
    return () => {
      window.removeEventListener("popstate", on);
      window.removeEventListener("hashchange", on);
      window.removeEventListener(ROUTE_CHANGE, on);
      document.removeEventListener("click", click);
    };
  }, []);
  return route;
}

/** Opens `dialog` as a modal and holds the page behind it still: the page is pinned at its
 * scroll position and keeps its scrollbar (with nothing to scroll), so it does not shift
 * sideways. Released when the dialog closes, or by the returned function (idempotent). */
export function showModal(dialog: HTMLDialogElement): () => void {
  const y = window.scrollY;
  const root = document.documentElement.style;
  const body = document.body.style;
  if (document.documentElement.scrollHeight > window.innerHeight) root.overflowY = "scroll";
  Object.assign(body, { position: "fixed", top: `-${y}px`, left: "0", right: "0" });
  let held = true;
  const release = () => {
    if (!held) return;
    held = false;
    root.overflowY = "";
    Object.assign(body, { position: "", top: "", left: "", right: "" });
    window.scrollTo({ top: y, behavior: "instant" });
  };
  dialog.addEventListener("close", release, { once: true });
  if (!dialog.open) dialog.showModal();
  return release;
}
