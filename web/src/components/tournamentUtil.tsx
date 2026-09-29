/** Labels and small pieces shared by the tournament views. */
import type { MsgKey, TFunc } from "../i18n";
import { fmtDate } from "../format";
import { playerHref } from "../hooks";
import type { AgeInfo, DrawPlayer, TournamentEvent } from "../types";
import { CategoryBadge } from "./common";

/** 1 MS, 2 WS, 3 MD, 4 WD, 5 mixed. */
export function matchTypeLabel(id: number, t: TFunc): string {
  const key = `mt_${id}` as MsgKey;
  const s = t(key);
  return s === key ? String(id) : s;
}

/** Senior age categories: the men's and the women's age limits (Swiss Tennis ids, not in age order). */
const SENIOR_AGES: Record<number, [number, number]> = {
  7: [35, 30],
  16: [40, 35],
  8: [45, 40],
  9: [50, 45],
  10: [55, 50],
  11: [60, 55],
  12: [65, 60],
  13: [70, 65],
  14: [75, 70],
  15: [80, 75],
  17: [85, 80],
  18: [90, 85],
};

/** Junior age categories 2–6: the age limit ("U18"). */
const JUNIOR_MAX: Record<number, number> = { 2: 18, 3: 16, 4: 14, 5: 12, 6: 10 };

/** The age info of a category: the server's (parsed from the official texts), else derived from the known ids. */
export function ageInfoOf(id: number, info?: AgeInfo | null): AgeInfo | null {
  if (info) return info;
  if (id === 1) return { id, kind: "actives" };
  if (JUNIOR_MAX[id]) return { id, kind: "juniors", max: JUNIOR_MAX[id] };
  const sen = SENIOR_AGES[id];
  if (sen) return { id, kind: "seniors", men: sen[0], women: sen[1] };
  return null;
}

/**
 * "Actives", "Juniors U16", "Young seniors 35+/30+", "Seniors 45+": for seniors, the men's or the
 * women's limit when the event says which (match type), both otherwise.
 */
export function ageLabel(id: number, t: TFunc, matchType?: number, info?: AgeInfo | null): string {
  const a = ageInfoOf(id, info);
  if (a?.kind === "actives") return t("age_1");
  if (a?.kind === "juniors" && a.max) return t("ageJuniors", { max: a.max });
  if (a?.kind === "seniors" && (a.men || a.women)) {
    const m = a.men ?? a.women;
    const w = a.women ?? a.men;
    let limits = m === w ? `${m}+` : `${m}+/${w}+`;
    if (matchType === 1 || matchType === 3) limits = `${m}+`;
    else if (matchType === 2 || matchType === 4) limits = `${w}+`;
    return t(m === 35 ? "ageYoungSeniors" : "ageSeniors", { limits });
  }
  const key = `age_${id}` as MsgKey;
  const s = t(key);
  return s === key ? String(id) : s;
}

/** Tooltip of a senior age category: "Men 45+, women 40+". */
export function ageTitle(id: number, t: TFunc, info?: AgeInfo | null): string | undefined {
  const a = ageInfoOf(id, info);
  return a?.kind === "seniors" && a.men && a.women ? t("ageSeniorTitle", { m: a.men, w: a.women }) : undefined;
}

/** Sort key of an age category: actives, juniors oldest first, then seniors youngest first. */
export function ageOrder(id: number, info?: AgeInfo | null): number {
  const a = ageInfoOf(id, info);
  if (a?.kind === "actives") return 0;
  if (a?.kind === "juniors") return 50 - (a.max ?? 0);
  if (a?.kind === "seniors") return 100 + (a.men ?? a.women ?? 0);
  return 1000 + id;
}

/** Singles: only those have an outlook. */
export function isSingles(matchType: number): boolean {
  return matchType === 1 || matchType === 2;
}

/** "R5–R1" (weakest bound first), or one category when both bounds are the same. */
export function rangeLabel(lower: string | null, upper: string | null): string {
  if (!lower && !upper) return "";
  if (!lower || !upper || lower === upper) return (lower ?? upper) as string;
  return `${lower}–${upper}`;
}

/** Title of an event: the official competition name, else built from its type, age and bounds. */
export function eventTitle(e: Pick<TournamentEvent, "competition" | "matchType" | "age" | "lower" | "upper" | "ageInfo">, t: TFunc): string {
  if (e.competition) return e.competition;
  return [matchTypeLabel(e.matchType, t), ageLabel(e.age, t, e.matchType, e.ageInfo), rangeLabel(e.lower, e.upper)].filter(Boolean).join(" · ");
}

/** Type and age of an event in words ("Men's singles · Actives"). */
export function eventKind(e: Pick<TournamentEvent, "matchType" | "age" | "ageInfo">, t: TFunc): string {
  return `${matchTypeLabel(e.matchType, t)} · ${ageLabel(e.age, t, e.matchType, e.ageInfo)}`;
}

/** Round counted from the final: 1 final, 2 semi-finals, 3 quarter-finals, then "round of 16"… */
/** Swiss Tennis gives the surfaces in French. */
const SURFACES_EN: Record<string, string> = {
  Autres: "Other",
  "Dur (asphalt/béton)": "Hard (asphalt/concrete)",
  "Dur (synthétique)": "Hard (synthetic)",
  "Gazon synthétique (sable de quartz ou de céramique)": "Artificial grass (quartz or ceramic sand)",
  "Moquette (avec granules)": "Carpet (with granules)",
  "Moquette (sans granules)": "Carpet (without granules)",
  "Terre battue": "Clay",
  "Terre battue (synthétique)": "Clay (synthetic)",
  "voir Infos supplémentaires": "see additional info",
};

export function surfaceLabel(s: string, lang: string): string {
  return lang === "fr" ? s : (SURFACES_EN[s] ?? s);
}

export function roundLabel(round: number, t: TFunc): string {
  if (round === 1) return t("roundFinal");
  if (round === 2) return t("roundSemi");
  if (round === 3) return t("roundQuarter");
  if (round > 3 && round < 12) return t("roundOf", { n: 2 ** round, m: 2 ** (round - 1) }); // French names the round by its matches (8es = last 16)
  return t("roundN", { n: round });
}

export function dateRange(from: string | null | undefined, to: string | null | undefined, locale: string): string {
  if (!from || !to || from === to) return fmtDate(from ?? to, locale);
  return `${fmtDate(from, locale)} – ${fmtDate(to, locale)}`;
}

/** Local date as YYYY-MM-DD. */
export function isoDay(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** A player (or a doubles pair) of a draw, linked to their page when known. */
export function DrawNames({ side, badge = true }: { side: DrawPlayer[]; badge?: boolean }) {
  if (!side.length) return <span className="muted">–</span>;
  return (
    <span className="draw-names">
      {side.map((p, i) => (
        <span key={i} className="draw-name">
          {i > 0 && <span className="muted"> / </span>}
          {p.personId != null ? <a href={playerHref(p.personId)}>{p.name}</a> : <span>{p.name}</span>}
          {badge && p.class && (
            <>
              {" "}
              <CategoryBadge cls={p.class} size="sm" />
            </>
          )}
        </span>
      ))}
    </span>
  );
}
