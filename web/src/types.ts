export type Gender = "M" | "F";
export type Result = "win" | "loss";
export type How = "normal" | "walkover" | "retired";
export type MatchType = "interclub" | "tournament" | "abroad" | "club" | "other";
export type Reason = "walkover" | "discarded_loss" | "no_value";

export interface ScrapeInfo {
  running: boolean;
  last_run: string | null;
  last_error: string | null;
  next_run: string | null;
}

export interface Meta {
  today: string;
  official_date: string;
  next_official: string;
  window_start: string;
  data_updated: string | null;
  scrape?: ScrapeInfo | null;
}

export interface PlayerSummary {
  id: number;
  name: string;
  licence: string | null;
  gender: Gender;
  class_official: string | null;
  class_today: string | null;
}

export interface OfficialRanking {
  date: string;
  class: string | null;
  rank: number | null;
  W: number | null;
  C: number | null;
}

export interface TodayRanking extends OfficialRanking {
  R: number | null;
  w0: number | null;
  n_matches: number | null;
  note: null | "assigned" | "classified";
}

export interface HistoryPoint {
  date: string;
  class: string | null;
  rank: number | null;
  W: number | null;
  C: number | null;
}

export interface PlayerDetail {
  id: number;
  name: string;
  licence: string | null;
  gender: Gender;
  official: OfficialRanking | null;
  today: TodayRanking | null;
  history: HistoryPoint[];
}

export interface OpponentRef {
  id: number | null;
  name: string;
  class: string | null;
  /** category on the official list in force (matches only) */
  class_official?: string | null;
  value?: number | null;
}

export interface Match {
  id: number;
  date: string;
  tournament: string;
  type: MatchType | string;
  opponent: OpponentRef;
  score: string;
  result: Result;
  how: How;
  counted: boolean;
  reason: Reason | null;
  delta_W: number | null;
  delta_C: number | null;
  last_list: string | null;
  drops_after_next_official: boolean;
}

export interface MatchesResponse {
  window: { start: string; end: string; next_official: string };
  matches: Match[];
}

export interface SimState {
  W: number | null;
  C: number | null;
  rank: number | null;
  class: string | null;
}

export interface SimSide {
  before: SimState;
  win: SimState;
  loss: SimState;
}

export interface SimulateResponse {
  player: SimSide;
  opponent: SimSide;
}

export interface WL {
  won: number;
  lost: number;
}

export interface NotableMatch {
  date: string;
  tournament: string;
  opponent: OpponentRef;
  opponent_value: number | null;
  score: string;
}

export interface Stats {
  range: string;
  years: number[];
  matches: {
    played: number;
    won: number;
    lost: number;
    walkovers_won: number;
    walkovers_lost: number;
    retired_won: number;
    retired_lost: number;
  };
  sets: WL;
  games: WL;
  two_sets: WL;
  three_sets: WL;
  after_first_set: {
    won_first: { played: number; won: number };
    lost_first: { played: number; won: number };
  };
  deciding_set: WL;
  match_tiebreak: WL;
  tiebreaks: WL;
  sets_7_5: WL;
  bagels: { given: number; received: number };
  breadsticks: { given: number; received: number };
  streak: {
    current: number;
    current_type: Result | null;
    longest_win: number;
    longest_loss: number;
  };
  by_relation: (WL & { relation: "higher" | "same" | "lower" | "unknown" })[];
  by_class: (WL & { class: string | null })[];
  by_type: (WL & { type: string })[];
  by_year: (WL & { year: number })[];
  by_month: (WL & { month: string })[];
  best_wins: NotableMatch[];
  worst_losses: NotableMatch[];
  opponents: (WL & { id: number | null; name: string; last_date: string })[];
}

export interface H2HMatch {
  date: string;
  tournament: string;
  score: string;
  result: Result;
  how: How;
}
