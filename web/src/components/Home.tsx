import { navigate, playerHref } from "../hooks";
import { useI18n } from "../i18n";
import { fmtDate, fmtDateTime } from "../format";
import type { Meta } from "../types";
import SearchBox from "./SearchBox";
import { Card } from "./common";

/** Backtest of the April 2026 list (README), predicted as today's list is: rank error per
 * category group (the only list whose licence holders, hence ranks, are known), share of players
 * with C within 0.001, and the monthly lists of one R1/R2 player. */
const LIST = "2026-04";
const RANKS = [
  { group: "N1–N4", n: 228, within10: 0.991, median: 1 },
  { group: "R1–R3", n: 1677, within10: 0.942, median: 2 },
  { group: "R4–R6", n: 12683, within10: 0.515, median: 10 },
];
const ALL = { n: 84886, within10: 0.1, median: 82 };
const TOP_WITHIN10 = 0.948; // N1–R3
const EXACT_C = 0.961;
const MONTHLY_RANK = 5;

function Accuracy() {
  const { t, locale } = useI18n();
  const pct = (v: number) => new Intl.NumberFormat(locale, { style: "percent", minimumFractionDigits: 1 }).format(v);
  const int = (v: number) => v.toLocaleString(locale);
  const list = new Date(`${LIST}-01T00:00:00`).toLocaleDateString(locale, { month: "long", year: "numeric" });
  return (
    <Card title={t("accTitle")} className="accuracy">
      <p className="hint small acc-intro">{t("accIntro", { date: list })}</p>
      <div className="meta-grid acc-grid">
        <div className="meta-tile">
          <small>{t("accTop")}</small>
          <strong className="acc-big">{pct(TOP_WITHIN10)}</strong>
          <small className="muted">{t("accTopNote")}</small>
        </div>
        <div className="meta-tile">
          <small>{t("accMedian")}</small>
          <strong className="acc-big">{t("accPlaces", { n: int(ALL.median) })}</strong>
          <small className="muted">{t("accMedianNote")}</small>
        </div>
        <div className="meta-tile">
          <small>{t("accExact")}</small>
          <strong className="acc-big">{pct(EXACT_C)}</strong>
          <small className="muted">{t("accExactNote")}</small>
        </div>
        <div className="meta-tile">
          <small>{t("accMonthly")}</small>
          <strong className="acc-big">±{MONTHLY_RANK}</strong>
          <small className="muted">{t("accMonthlyNote")}</small>
        </div>
      </div>
      <div className="table-wrap">
        <table className="data acc-table">
          <thead>
            <tr>
              <th>{t("accGroup")}</th>
              <th className="num">{t("accPlayers")}</th>
              <th className="num">{t("accWithin10")}</th>
              <th className="num">{t("accMedianCol")}</th>
            </tr>
          </thead>
          <tbody>
            {[...RANKS, { ...ALL, group: t("accAll") }].map((r) => (
              <tr key={r.group}>
                <td>{r.group}</td>
                <td className="num">{int(r.n)}</td>
                <td className="num">{pct(r.within10)}</td>
                <td className="num">{int(r.median)}</td>
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
