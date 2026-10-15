import type {
  Career,
  ClubDetail,
  ClubHit,
  ListedMatch,
  Highlights,
  MatchesResponse,
  Meta,
  MoveUp,
  Outlook,
  PlayerDetail,
  PlayerSummary,
  PredictResponse,
  SimulateResponse,
  Stats,
  TournamentDetail,
  TournamentHit,
  TournamentList,
  PlayerTournaments,
} from "./types";
import { timedRequest } from "./perf";

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
  const data = await timedRequest(fetchJson<T>(path, signal));
  if (useCache) {
    if (cache.size >= CACHE_MAX) cache.delete(cache.keys().next().value as string);
    cache.set(path, data);
  }
  return data;
}

async function fetchJson<T>(path: string, signal?: AbortSignal): Promise<T> {
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
  return (await res.json()) as T;
}

export const api = {
  meta: (signal?: AbortSignal) => get<Meta>("/api/meta", signal, false),
  search: (q: string, limit = 20, signal?: AbortSignal, gender?: string) =>
    get<PlayerSummary[]>(
      `/api/players/search?q=${encodeURIComponent(q)}&limit=${limit}${gender ? `&gender=${gender}` : ""}`,
      signal,
    ),
  tournamentSearch: (q: string, limit = 20, signal?: AbortSignal) =>
    get<TournamentHit[]>(`/api/tournaments/search?q=${encodeURIComponent(q)}&limit=${limit}`, signal),
  player: (id: number, signal?: AbortSignal) =>
    get<PlayerDetail>(`/api/players/${id}`, signal),
  matches: (id: number, season: number, signal?: AbortSignal) =>
    get<MatchesResponse>(`/api/players/${id}/matches?season=${season}`, signal),
  career: (id: number, signal?: AbortSignal) => get<Career>(`/api/players/${id}/career`, signal),
  /** what-if: made-up results, played matches left out (ids), matches against players (win:id) */
  predict: (id: number, q: { results: string; remove: string; add: string }, signal?: AbortSignal) =>
    get<PredictResponse>(`/api/players/${id}/predict?${new URLSearchParams(q)}`, signal),
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
  highlights: (age: string, signal?: AbortSignal) =>
    get<Highlights>(`/api/highlights${age ? `?age=${age}` : ""}`, signal),
  clubs: (signal?: AbortSignal) => get<ClubHit[]>("/api/clubs", signal),
  clubSearch: (q: string, limit = 20, signal?: AbortSignal) =>
    get<ClubHit[]>(`/api/clubs/search?q=${encodeURIComponent(q)}&limit=${limit}`, signal),
  club: (id: number, signal?: AbortSignal) => get<ClubDetail>(`/api/clubs/${id}`, signal),
  simulate: (player: number, opponent: number, signal?: AbortSignal) =>
    get<SimulateResponse>(`/api/simulate?player=${player}&opponent=${opponent}`, signal),
  /** `query` is a URL query string without "?" (the filters of the tournaments page). */
  tournaments: (query: string, signal?: AbortSignal) =>
    get<TournamentList>(`/api/tournaments${query ? `?${query}` : ""}`, signal),
  tournament: (id: number, signal?: AbortSignal) => get<TournamentDetail>(`/api/tournaments/${id}`, signal),
  playerTournaments: (id: number, signal?: AbortSignal) =>
    get<PlayerTournaments>(`/api/players/${id}/tournaments`, signal),
  outlook: (id: number, eventId: number, signal?: AbortSignal) =>
    get<Outlook>(`/api/players/${id}/tournaments/events/${eventId}/outlook`, signal),
};
