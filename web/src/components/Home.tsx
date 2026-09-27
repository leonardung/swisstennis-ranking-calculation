import { navigate, playerHref } from "../hooks";
import { useI18n } from "../i18n";
import { fmtDate, fmtDateTime } from "../format";
import type { Meta } from "../types";
import SearchBox from "./SearchBox";
import { Card } from "./common";

/** Backtests against the published lists (README): predict mode, share of players with C within
 * 0.001 and in the right category; reproduce mode, right category; monthly lists of one player. */
const PREDICTED = [
  { list: "2021-10", exact: 0.688, category: 0.979 },
  { list: "2023-04", exact: 0.657, category: 0.958 },
  { list: "2024-04", exact: 0.847, category: 0.958 },
  { list: "2025-04", exact: 0.946, category: 0.961 },
  { list: "2025-10", exact: 0.944, category: 0.976 },
  { list: "2026-04", exact: 0.961, category: 0.99 },
];
const REPRODUCED_CATEGORY = 0.999;
const MONTHLY = { c: 0.013, rank: 5 };

function Accuracy() {
  const { t, locale } = useI18n();
  const pct = (v: number) => new Intl.NumberFormat(locale, { style: "percent", minimumFractionDigits: 1 }).format(v);
  const month = (ym: string) => new Date(`${ym}-01T00:00:00`).toLocaleDateString(locale, { month: "long", year: "numeric" });
  const last = PREDICTED[PREDICTED.length - 1];
  const lastDate = month(last.list);
  return (
    <Card title={t("accTitle")} className="accuracy">
      <p className="hint small acc-intro">{t("accIntro")}</p>
      <div className="meta-grid acc-grid">
        <div className="meta-tile">
          <small>{t("accCategory")}</small>
          <strong className="acc-big">{pct(last.category)}</strong>
          <small className="muted">{t("accCategoryNote", { date: lastDate })}</small>
        </div>
        <div className="meta-tile">
          <small>{t("accExact")}</small>
          <strong className="acc-big">{pct(last.exact)}</strong>
          <small className="muted">{t("accExactNote", { date: lastDate })}</small>
        </div>
        <div className="meta-tile">
          <small>{t("accReproduce")}</small>
          <strong className="acc-big">{pct(REPRODUCED_CATEGORY)}</strong>
          <small className="muted">{t("accReproduceNote")}</small>
        </div>
        <div className="meta-tile">
          <small>{t("accMonthly")}</small>
          <strong className="acc-big">±{MONTHLY.rank}</strong>
          <small className="muted">
            {t("accMonthlyNote", { c: MONTHLY.c.toLocaleString(locale) })}
          </small>
        </div>
      </div>
      <div className="table-wrap">
        <table className="data acc-table">
          <thead>
            <tr>
              <th>{t("accList")}</th>
              <th className="num">{t("accPredExact")}</th>
              <th className="num">{t("accPredCategory")}</th>
            </tr>
          </thead>
          <tbody>
            {PREDICTED.map((r) => (
              <tr key={r.list}>
                <td>{month(r.list)}</td>
                <td className="num">{pct(r.exact)}</td>
                <td className="num">{pct(r.category)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="hint small">{t("accFoot")}</p>
    </Card>
  );
}

export default function Home({ meta }: { meta: Meta | null }) {
  const { t, locale } = useI18n();
  return (
    <div className="home">
      <section className="hero">
        <h1>{t("homeTitle")}</h1>
        <p className="muted">{t("homeText")}</p>
        <SearchBox className="hero-search" autoFocus onSelect={(p) => navigate(playerHref(p.id))} />
      </section>
      {meta && (
        <section className="meta-grid">
          <div className="meta-tile">
            <small>{t("homeCurrentList")}</small>
            <strong>{fmtDate(meta.official_date, locale)}</strong>
          </div>
          <div className="meta-tile">
            <small>{t("homeNextList")}</small>
            <strong>{fmtDate(meta.next_official, locale)}</strong>
          </div>
          <div className="meta-tile">
            <small>{t("homeWindow")}</small>
            <strong>
              {fmtDate(meta.window_start, locale)} → {fmtDate(meta.today, locale)}
            </strong>
          </div>
          <div className="meta-tile">
            <small>{t("dataUpdated")}</small>
            <strong>{fmtDateTime(meta.data_updated, locale)}</strong>
            {meta.scrape?.last_error && (
              <small className="neg" title={meta.scrape.last_error}>
                {t("scrapeError")}
              </small>
            )}
            {meta.scrape?.next_run && (
              <small className="muted">
                {t("scrapeNext")}: {fmtDateTime(meta.scrape.next_run, locale)}
              </small>
            )}
          </div>
        </section>
      )}
      <Accuracy />
    </div>
  );
}
