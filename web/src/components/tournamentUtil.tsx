/** Labels and small pieces shared by the tournament views. */
import type { MsgKey, TFunc } from "../i18n";
import { fmtDate } from "../format";
import { playerHref } from "../hooks";
import type { DrawPlayer, TournamentEvent } from "../types";
import { CategoryBadge } from "./common";

/** 1 MS, 2 WS, 3 MD, 4 WD, 5 mixed. */
export function matchTypeLabel(id: number, t: TFunc): string {
  const key = `mt_${id}` as MsgKey;
  const s = t(key);
  return s === key ? String(id) : s;
}

/** 1 actives, 2–6 juniors I–V, 7 young seniors, 8+ seniors I, II, … */
export function ageLabel(id: number, t: TFunc): string {
  const key = `age_${id}` as MsgKey;
  const s = t(key);
  if (s !== key) return s;
  return id > 7 ? t("ageSeniorsN", { n: id - 7 }) : String(id);
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
export function eventTitle(e: Pick<TournamentEvent, "competition" | "matchType" | "age" | "lower" | "upper">, t: TFunc): string {
  if (e.competition) return e.competition;
  return [matchTypeLabel(e.matchType, t), ageLabel(e.age, t), rangeLabel(e.lower, e.upper)].filter(Boolean).join(" · ");
}

/** Type and age of an event in words ("Men's singles · Actives"). */
export function eventKind(e: Pick<TournamentEvent, "matchType" | "age">, t: TFunc): string {
  return `${matchTypeLabel(e.matchType, t)} · ${ageLabel(e.age, t)}`;
}

/** Round counted from the final: 1 final, 2 semi-finals, 3 quarter-finals, then "round of 16"… */
export function roundLabel(round: number, t: TFunc): string {
  if (round === 1) return t("roundFinal");
  if (round === 2) return t("roundSemi");
  if (round === 3) return t("roundQuarter");
  if (round > 3 && round < 12) return t("roundOf", { n: 2 ** round });
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
