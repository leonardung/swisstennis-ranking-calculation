import type {
  Career,
  ListedMatch,
  Highlights,
  MatchesResponse,
  Meta,
  MoveUp,
  PlayerDetail,
  PlayerSummary,
  SimState,
  SimulateResponse,
  Stats,
} from "./types";

/** Error carrying the HTTP status so callers can distinguish 404 / 503 ("loading"). */
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
  get loading(): boolean {
    return this.status === 503;
  }
}

/** Resolved responses, keyed by URL (cleared when the backend data changes). */
const cache = new Map<string, unknown>();
const CACHE_MAX = 300;

export function clearCache(): void {
  cache.clear();
}

async function get<T>(path: string, signal?: AbortSignal, useCache = true): Promise<T> {
  if (useCache && cache.has(path)) return cache.get(path) as T;
  let res: Response;
  try {
    res = await fetch(path, { signal, headers: { Accept: "application/json" } });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "network");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  const data = (await res.json()) as T;
  if (useCache) {
    if (cache.size >= CACHE_MAX) cache.delete(cache.keys().next().value as string);
    cache.set(path, data);
  }
  return data;
}

export const api = {
  meta: (signal?: AbortSignal) => get<Meta>("/api/meta", signal, false),
  search: (q: string, limit = 20, signal?: AbortSignal) =>
    get<PlayerSummary[]>(
      `/api/players/search?q=${encodeURIComponent(q)}&limit=${limit}`,
      signal,
    ),
  player: (id: number, signal?: AbortSignal) =>
    get<PlayerDetail>(`/api/players/${id}`, signal),
  matches: (id: number, season: number, signal?: AbortSignal) =>
    get<MatchesResponse>(`/api/players/${id}/matches?season=${season}`, signal),
  career: (id: number, signal?: AbortSignal) => get<Career>(`/api/players/${id}/career`, signal),
  predict: (id: number, results: string, signal?: AbortSignal) =>
    get<{ before: SimState; after: SimState }>(`/api/players/${id}/predict?results=${encodeURIComponent(results)}`, signal),
  moveUp: (id: number, signal?: AbortSignal) => get<MoveUp | null>(`/api/players/${id}/move-up`, signal),
  stats: (id: number, range: string, signal?: AbortSignal) =>
    get<Stats>(`/api/players/${id}/stats?range=${encodeURIComponent(range)}`, signal),
  h2h: (id: number, opponentId: number, signal?: AbortSignal) =>
    get<ListedMatch[]>(`/api/players/${id}/h2h/${opponentId}`, signal),
  statMatches: (id: number, range: string, key: string, value: string | undefined, signal?: AbortSignal) =>
    get<ListedMatch[]>(
      `/api/players/${id}/stats/matches?range=${encodeURIComponent(range)}&key=${key}` +
        (value != null ? `&value=${encodeURIComponent(value)}` : ""),
      signal,
    ),
  boardMatches: (key: string, id: number, signal?: AbortSignal) =>
    get<ListedMatch[]>(`/api/highlights/${key}/${id}`, signal),
  highlights: (signal?: AbortSignal) => get<Highlights>("/api/highlights", signal),
  simulate: (player: number, opponent: number, signal?: AbortSignal) =>
    get<SimulateResponse>(`/api/simulate?player=${player}&opponent=${opponent}`, signal),
};
