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

export type Tab = "overview" | "matches" | "simulator" | "stats";
export const TABS: Tab[] = ["overview", "matches", "simulator", "stats"];

export type Route = { page: "home" } | { page: "player"; id: number; tab: Tab };

export function parsePath(path: string): Route {
  const m = /^\/player\/(\d+)(?:\/([a-z]+))?\/?$/.exec(path);
  if (m) {
    const tab = (TABS as string[]).includes(m[2] ?? "") ? (m[2] as Tab) : "overview";
    return { page: "player", id: Number(m[1]), tab };
  }
  return { page: "home" };
}

export function playerHref(id: number, tab?: Tab): string {
  return tab && tab !== "overview" ? `/player/${id}/${tab}` : `/player/${id}`;
}

const ROUTE_CHANGE = "routechange";

export function navigate(href: string): void {
  if (window.location.pathname === href) return;
  window.history.pushState(null, "", href);
  window.dispatchEvent(new Event(ROUTE_CHANGE));
}

// Links shared before the switch from hash routes (#/player/1) keep working.
if (window.location.hash.startsWith("#/")) {
  window.history.replaceState(null, "", window.location.hash.slice(1));
}

/** The current route; plain same-site links navigate without reloading the page. */
export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parsePath(window.location.pathname));
  useEffect(() => {
    const on = () => setRoute(parsePath(window.location.pathname));
    const click = (e: MouseEvent) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const a = (e.target as Element).closest?.("a");
      if (!a || a.target || a.hasAttribute("download") || a.origin !== window.location.origin) return;
      e.preventDefault();
      navigate(a.pathname);
    };
    window.addEventListener("popstate", on);
    window.addEventListener(ROUTE_CHANGE, on);
    document.addEventListener("click", click);
    return () => {
      window.removeEventListener("popstate", on);
      window.removeEventListener(ROUTE_CHANGE, on);
      document.removeEventListener("click", click);
    };
  }, []);
  return route;
}
