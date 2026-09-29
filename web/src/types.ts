export type Gender = "M" | "F";
export type Result = "win" | "loss";
export type How = "normal" | "walkover" | "retired";
export type MatchType = "interclub" | "tournament" | "abroad" | "club" | "other";
export type Reason = "walkover" | "discarded_loss" | "no_value" | "past";

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
  tournaments?: { updated: string | null; running: boolean; lastError: string | null } | null;
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

export interface OfficialRanking {
  /** false: not on the list in force, these are the latest list the player was on */
  current?: boolean;
}

export interface FreeValues {
  W: number | null;
  R: number | null;
  C: number | null;
  rank: number | null;
  class: string | null;
}

export interface TodayRanking extends OfficialRanking {
  R: number | null;
  w0: number | null;
  n_matches: number | null;
  note: null | "assigned" | "classified";
  /** assigned value (foreigner): the values the matches would give, for information */
  free: FreeValues | null;
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

/** Category and rank on the list in force at a match. */
export interface Standing {
  class: string | null;
  rank: number | null;
}

export interface OpponentRef {
  id: number | null;
  name: string;
  /** category on the list in force at the match */
  class: string | null;
  rank?: number | null;
  /** category on the official list in force, and today's (matches only) */
  class_official?: string | null;
  class_today?: string | null;
  value?: number | null;
}

export interface Match {
  id: number;
  date: string;
  tournament: string | null;
  type: MatchType | string;
  opponent: OpponentRef;
  /** the player at the match */
  own: Standing;
  score: string;
  result: Result;
  how: How;
  /** the chance of winning before the match (none for walkovers or without a model) */
  chance: number | null;
  counted: boolean;
  reason: Reason | null;
  delta_W: number | null;
  delta_C: number | null;
  last_list: string | null;
  drops_after_next_official: boolean;
}

export interface Season {
  start: string;
  end: string;
}

export interface MatchesResponse {
  /** 0: today's window (what each match counts); 1, 2...: the year windows before it */
  season: number;
  seasons: Season[];
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

export interface PredictResponse {
  before: SimState;
  after: SimState;
  /** played matches left out, results added (made up and against players) */
  removed: number;
  added: number;
}

export interface SimulateResponse {
  player: SimSide;
  opponent: SimSide;
  /** the player's chance of beating the opponent (none without a model) */
  chance: number | null;
}

export interface WL {
  won: number;
  lost: number;
}

export interface NotableMatch {
  date: string;
  tournament: string | null;
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
  /** class: on today's list */
  opponents: (WL & { id: number | null; name: string; last_date: string; class: string | null })[];
}

/** A match in a popup list (the matches behind a statistic or a leaderboard row). */
export interface ListedMatch {
  date: string;
  tournament: string | null;
  opponent: OpponentRef;
  own: Standing;
  score: string;
  result: Result;
  how: How;
}

export interface CareerWin {
  date: string;
  tournament: string | null;
  opponent: OpponentRef;
  own: Standing;
  score: string;
  opponent_value?: number | null;
}

export interface Career {
  matches: number;
  won: number;
  lost: number;
  first_year: number | null;
  last_year: number | null;
  /** years with matches */
  seasons: number;
  /** the wins against the strongest opponents (W then), the strongest first */
  best_wins: CareerWin[];
  /** the first win against each category (N1 to R8) */
  first_wins: CareerWin[];
}

export interface MoveUp {
  class: string;
  target: string;
  C: number;
  /** the lowest C in the target category */
  need: number;
  max_wins: number;
  levels: {
    class: string;
    /** wins needed against a player at that percentile of W (null: more than max_wins) */
    opponents: { percentile: number; W: number; wins: number | null }[];
  }[];
}

export interface PersonRef {
  id: number;
  name: string;
}

export type BoardKey = "tiebreaks" | "longest_super_tiebreak" | "biggest_upset" | "three_sets" | "bagels" | "streak";

export interface BoardRow {
  player: PersonRef & { class: string | null };
  value: number;
  won?: number;
  lost?: number;
  start?: string;
  end?: string;
  ongoing?: boolean;
  score?: string; // longest match tiebreak: the player's points first; biggest upset: the match's
  date?: string;
  opponent?: PersonRef & { class: string }; // biggest upset: the beaten player, category then
}

export interface Upset {
  gender: string;
  group: HlGroup;
  date: string;
  winner: PersonRef & { class: string; W: number | null };
  loser: PersonRef & { class: string; W: number | null };
  levels: number;
  score: string;
  C: number | null; // what the win brings the winner today
  chance: number | null; // the winner's chance before the match
}

export type HlGroup = "N1-R3" | "R4-R9";

export interface ListPoint {
  class: string;
  rank: number | null;
  C: number | null;
}

export interface Mover {
  gender: string;
  group: HlGroup;
  player: PersonRef;
  from: ListPoint;
  to: ListPoint;
  levels: number;
  gain: number | null;
  won: number;
  lost: number;
}

export interface MoverList {
  from: string;
  to: string;
  players: Mover[];
}

export interface Highlights {
  leaderboards: { since: string; min_played: Partial<Record<BoardKey, number>>; players: Record<string, Record<BoardKey, BoardRow[]>> };
  upsets: { from: string; to: string; matches: Upset[] };
  movers: { today: MoverList; season?: MoverList };
}

// ---------- Tournaments ----------

export interface TournamentEvent {
  id: number;
  matchType: number;
  age: number;
  lower: string | null;
  upper: string | null;
  competition: string | null;
  players: number;
  inRange: number | null;
  registrationOpen: boolean | null;
  hasDraw: boolean;
  /** Parsed age category (null/absent when unknown or on an older server). */
  ageInfo?: AgeInfo | null;
}

/** An age category: actives, juniors up to `max` ("U16"), or seniors with the men's and women's limits. */
export interface AgeInfo {
  id: number;
  kind: "actives" | "juniors" | "seniors" | string;
  men?: number | null;
  women?: number | null;
  max?: number | null;
}

export type TournamentWhen = "upcoming" | "live" | "past";

export interface TournamentSummary {
  id: number;
  name: string;
  city: string | null;
  canton: string | null;
  dateBegin: string;
  dateEnd: string;
  status: number;
  distanceKm: number | null;
  players: number;
  inRange: number | null;
  surface?: string | null;
}

export interface TournamentListItem extends TournamentSummary {
  events: TournamentEvent[];
}

export interface TournamentOptions {
  matchTypes: number[];
  /** AgeInfo items; plain ids on an older server. */
  ages: (AgeInfo | number)[];
  surfaces?: string[];
  cantons: string[];
  cities: string[];
}

export interface TournamentList {
  total: number;
  tournaments: TournamentListItem[];
  options: TournamentOptions;
  updated: string | null;
}

export interface DrawPlayer {
  personId: number | null;
  name: string;
  class: string | null;
}

export interface DrawMatch {
  slot: number;
  a: DrawPlayer[];
  b: DrawPlayer[];
  winner: 1 | 2 | null;
  score: string | null;
  walkover: boolean;
  date: string | null;
}

export interface DrawRound {
  round: number;
  matches: DrawMatch[];
}

export interface RegisteredPlayer {
  personId: number | null;
  licence: string | null;
  name: string;
  class: string | null;
  officialClass: string | null;
  C: number | null;
}

export interface TournamentEventDetail {
  id: number;
  matchType: number;
  age: number;
  lower: string | null;
  upper: string | null;
  competition: string | null;
  dateFrom: string | null;
  dateTo: string | null;
  registrationOpen: boolean | null;
  pdfUrl: string | null;
  ageInfo?: AgeInfo | null;
  applicants: number | null;
  players: RegisteredPlayer[];
  draw: { rounds: DrawRound[] } | null;
}

export interface TournamentDetail {
  id: number;
  name: string;
  city: string | null;
  canton: string | null;
  dateBegin: string;
  dateEnd: string;
  drawingTime: string | null;
  status: number;
  surface: string | null;
  place: string | null;
  category: string | null;
  selection: string | null;
  entryFee: string | null;
  organiser: string | null;
  email: string | null;
  phone: string | null;
  homePage: string | null;
  mytennisUrl: string | null;
  events: TournamentEventDetail[];
}

export interface PlayerTournamentEntry {
  tournament: TournamentSummary;
  event: TournamentEvent;
}

export interface PastResult {
  round: number;
  opponent: DrawPlayer[];
  win: boolean | null; // null: winner unknown
  score: string | null;
  walkover: boolean;
}

export interface PlayerTournaments {
  registered: PlayerTournamentEntry[];
  suggested: (PlayerTournamentEntry & { inRange: number })[];
  past: (PlayerTournamentEntry & { reached: number | null; results: PastResult[] })[];
  /** the player's clubs with a place: the suggestions are ranked around them */
  clubs: { name: string | null; city: string | null }[];
}

export interface OutlookOpponent {
  personId: number | null;
  name: string;
  class: string | null;
  W: number | null;
  chance: number | null;
  gainWin: number | null;
  lossLoss: number | null;
}

export interface Outlook {
  field: { count: number; medianW: number | null; topW: number | null; inRange: number | null };
  opponents: OutlookOpponent[];
  // null when no opponent has a value
  /** rounds the player plays (round: 1 = final), the chance to reach each and his change of C if he goes out there; title: its chance and change of C */
  expected: {
    gain: number | null;
    rounds: { round: number; reach: number; ifOut: number | null }[];
    title: number;
    ifTitle: number | null;
  } | null;
}
