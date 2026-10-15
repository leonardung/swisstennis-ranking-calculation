import { useId, type ReactNode } from "react";
import { ApiError } from "../api";
import { useUrlState } from "../hooks";
import { useI18n } from "../i18n";
import { categoryIndex, categoryMove, fmtDelta, fmtInt, signClass } from "../format";
import type { How, Result } from "../types";

export function CategoryBadge({ cls, size = "md" }: { cls: string | null | undefined; size?: "sm" | "md" | "lg" }) {
  const idx = categoryIndex(cls);
  return (
    <span className={`cat cat-${size} ${idx < 0 ? "cat-none" : `cat-${idx}`}`} title={cls ?? undefined}>
      {cls ?? "–"}
    </span>
  );
}

/** "R2 → R1 ↑" style category change. */
export function ClassChange({ from, to }: { from: string | null | undefined; to: string | null | undefined }) {
  const { t } = useI18n();
  const move = categoryMove(from, to);
  if (move === 0) return <CategoryBadge cls={to} size="sm" />;
  return (
    <span className={`class-change ${move > 0 ? "pos" : "neg"}`}>
      <CategoryBadge cls={from} size="sm" />
      <span aria-hidden>→</span>
      <CategoryBadge cls={to} size="sm" />
      <span className="arrow" title={move > 0 ? t("up") : t("down")}>
        {move > 0 ? "↑" : "↓"}
      </span>
    </span>
  );
}

export function Delta({ value, digits = 3 }: { value: number | null | undefined; digits?: number }) {
  return <span className={`delta ${signClass(value)}`}>{fmtDelta(value, digits)}</span>;
}

export function ResultPill({ result, how }: { result: Result; how?: How }) {
  const { t } = useI18n();
  return (
    <span className={`pill ${result === "win" ? "pill-win" : "pill-loss"}`}>
      {result === "win" ? t("win") : t("loss")}
      {how === "walkover" && <small> · {t("wo")}</small>}
      {how === "retired" && <small> · {t("ret")}</small>}
    </span>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="spinner-wrap" role="status" aria-live="polite">
      <span className="spinner" aria-hidden />
      {label && <span>{label}</span>}
    </div>
  );
}

export function BackendLoading() {
  const { t } = useI18n();
  return (
    <div className="state-screen">
      <div className="ball-bounce" aria-hidden />
      <h2>{t("loadingTitle")}</h2>
      <p>{t("loadingText")}</p>
    </div>
  );
}

/** Standard loading / error presentation for a useAsync state. Returns null when data is ready. */
export function AsyncStatus({
  loading,
  error,
  backendLoading,
  reload,
  notFound,
}: {
  loading: boolean;
  error: Error | null;
  backendLoading: boolean;
  reload: () => void;
  notFound?: ReactNode;
}) {
  const { t } = useI18n();
  if (backendLoading) return <BackendLoading />;
  if (error) {
    if (error instanceof ApiError && error.status === 404 && notFound) return <>{notFound}</>;
    const msg = error instanceof ApiError && error.status === 0 ? t("errorNetwork") : error.message;
    return (
      <div className="state-screen small">
        <h3>{t("errorTitle")}</h3>
        <p className="muted">{msg}</p>
        <button className="btn" onClick={reload}>
          {t("retry")}
        </button>
      </div>
    );
  }
  if (loading) return <Spinner label={t("loading")} />;
  return null;
}

/** A label with a short explanation shown on hover or keyboard focus. */
export function Hint({ label, text, align = "start" }: { label: ReactNode; text: string; align?: "start" | "end" }) {
  const id = useId();
  return (
    <span className="hint-tip" tabIndex={0} aria-describedby={id}>
      {label}
      <span role="tooltip" id={id} className={`hint-pop hint-pop-${align}`}>
        {text}
      </span>
    </span>
  );
}

export function Card({ title, children, className = "", actions }: { title?: ReactNode; children: ReactNode; className?: string; actions?: ReactNode }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          {title && <h3>{title}</h3>}
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

/** Category and rank on the list in force at a match ("R2 #412"). */
export function Standing({ cls, rank }: { cls: string | null | undefined; rank?: number | null }) {
  const { locale } = useI18n();
  return (
    <span className="standing">
      <CategoryBadge cls={cls} size="sm" />
      {rank != null && <small className="muted">#{fmtInt(rank, locale)}</small>}
    </span>
  );
}

/** Why a what-if leaves the player's values unchanged: Swiss Tennis sets them (assigned value of a
 * foreigner, classification of a new player). Nothing for a computed value. */
export function FixedValueNote({ note }: { note: "assigned" | "classified" | null | undefined }) {
  const { t } = useI18n();
  if (!note) return null;
  const reason = t(note === "assigned" ? "reasonAssigned" : "reasonClassified");
  return <p className="hint hint-info">ⓘ {t("noteFixedSim", { reason })}</p>;
}

/** A segmented control: one of a few options. */
export function Seg<T extends string>({ value, options, onChange }: { value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <div className="seg" role="group">
      {options.map(([k, label]) => (
        <button key={k} className={value === k ? "active" : ""} onClick={() => onChange(k)}>
          {label}
        </button>
      ))}
    </div>
  );
}

export interface Sort<K extends string> {
  key: K;
  desc: boolean;
}

/** A table's sort kept in the URL (?param=key-desc or key-asc): the sort, and a header cell that
 * sorts by its column (a second click reverses; `desc`: the first click's direction).
 * `onSort`: called on a click (e.g. back to the first page). */
export function useSort<K extends string>(
  param: string,
  initial: Sort<K>,
  onSort?: () => void,
): [Sort<K>, (key: K, label: ReactNode, opts?: { className?: string; desc?: boolean }) => ReactNode] {
  const [value, setValue] = useUrlState(param, `${initial.key}-${initial.desc ? "desc" : "asc"}`);
  const [key, dir] = value.split("-");
  const sort: Sort<K> = { key: key as K, desc: dir !== "asc" };
  const th = (k: K, label: ReactNode, { className = "", desc = true }: { className?: string; desc?: boolean } = {}) => {
    const on = sort.key === k;
    const next = on ? !sort.desc : desc;
    return (
      <th className={`sortable ${className}`} aria-sort={on ? (sort.desc ? "descending" : "ascending") : undefined}>
        <button type="button" className="sort-btn" onClick={() => {
            setValue(`${k}-${next ? "desc" : "asc"}`);
            onSort?.();
          }}
        >
          {label}
          <span className="sort-arrow" aria-hidden>
            {on ? (sort.desc ? "▼" : "▲") : ""}
          </span>
        </button>
      </th>
    );
  };
  return [sort, th];
}

/** The rows sorted by `value` (numbers or texts), missing values last either way. */
export function sortRows<T>(rows: T[], value: (r: T) => number | string | null | undefined, desc: boolean): T[] {
  const dir = desc ? -1 : 1;
  return [...rows].sort((a, b) => {
    const va = value(a);
    const vb = value(b);
    if (va == null || vb == null) return va == null ? (vb == null ? 0 : 1) : -1;
    const c = typeof va === "number" && typeof vb === "number" ? va - vb : String(va).localeCompare(String(vb));
    return c * dir;
  });
}

/** Previous / next for a list shown `limit` at a time from `offset`, with "21–40 / 831". */
export function Pager({ offset, limit, total, onChange }: { offset: number; limit: number; total: number; onChange: (offset: number) => void }) {
  const { t, locale } = useI18n();
  if (total <= limit) return null;
  return (
    <nav className="pager" aria-label={t("pages")}>
      <button className="btn" disabled={offset <= 0} onClick={() => onChange(Math.max(0, offset - limit))}>
        ‹ {t("previous")}
      </button>
      <span className="muted">
        {fmtInt(offset + 1, locale)}–{fmtInt(Math.min(offset + limit, total), locale)} / {fmtInt(total, locale)}
      </span>
      <button className="btn" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>
        {t("next")} ›
      </button>
    </nav>
  );
}
