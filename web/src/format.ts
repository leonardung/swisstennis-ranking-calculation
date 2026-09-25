/** Number, date and category helpers shared by all views. */

export const DASH = "–";

export function fmt3(n: number | null | undefined): string {
  return n == null || Number.isNaN(n) ? DASH : n.toFixed(3);
}

/** Signed delta with 3 decimals, e.g. +0.042 / −0.013. */
export function fmtDelta(n: number | null | undefined, digits = 3): string {
  if (n == null || Number.isNaN(n)) return DASH;
  const r = Number(n.toFixed(digits));
  if (r === 0) return (0).toFixed(digits);
  return (r > 0 ? "+" : "−") + Math.abs(r).toFixed(digits);
}

/** Signed integer delta (rank changes). */
export function fmtIntDelta(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return DASH;
  if (n === 0) return "0";
  return (n > 0 ? "+" : "−") + Math.abs(n).toLocaleString();
}

export function pctValue(part: number, total: number): number | null {
  return total > 0 ? (part / total) * 100 : null;
}

export function fmtPct(part: number, total: number): string {
  const p = pctValue(part, total);
  return p == null ? DASH : `${p.toFixed(1)}%`;
}

export function fmtInt(n: number | null | undefined, locale?: string): string {
  return n == null ? DASH : n.toLocaleString(locale);
}

function parseIsoDate(iso: string): Date {
  // Date-only strings parsed as local dates (avoid UTC shifts).
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (m) return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return new Date(iso);
}

export function fmtDate(iso: string | null | undefined, locale: string): string {
  if (!iso) return DASH;
  const d = parseIsoDate(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(locale, { day: "numeric", month: "short", year: "numeric" });
}

export function fmtDateShort(iso: string, locale: string): string {
  const d = parseIsoDate(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(locale, { month: "short", year: "numeric" });
}

export function fmtDateTime(iso: string | null | undefined, locale: string): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(locale, {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function fmtMonth(ym: string, locale: string): string {
  const m = /^(\d{4})-(\d{2})$/.exec(ym);
  if (!m) return ym;
  return new Date(Number(m[1]), Number(m[2]) - 1, 1).toLocaleDateString(locale, {
    month: "short",
    year: "2-digit",
  });
}

export const CATEGORIES = ["N1", "N2", "N3", "N4", "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9"];

/** 0 = best (N1) … 12 = R9; -1 when unknown. */
export function categoryIndex(cls: string | null | undefined): number {
  if (!cls) return -1;
  return CATEGORIES.indexOf(cls.toUpperCase());
}

/** Sign of a category change: 1 = promotion, -1 = relegation, 0 = same / unknown. */
export function categoryMove(from: string | null | undefined, to: string | null | undefined): number {
  const a = categoryIndex(from);
  const b = categoryIndex(to);
  if (a < 0 || b < 0 || a === b) return 0;
  return b < a ? 1 : -1;
}

export function signClass(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n) || Math.abs(n) < 0.0005) return "neutral";
  return n > 0 ? "pos" : "neg";
}
