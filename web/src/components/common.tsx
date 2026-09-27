import { useId, type ReactNode } from "react";
import { ApiError } from "../api";
import { useI18n } from "../i18n";
import { categoryIndex, categoryMove, fmtDelta, signClass } from "../format";
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
