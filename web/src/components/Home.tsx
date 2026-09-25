import { navigate, playerHref } from "../hooks";
import { useI18n } from "../i18n";
import { fmtDate, fmtDateTime } from "../format";
import type { Meta } from "../types";
import SearchBox from "./SearchBox";

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
    </div>
  );
}
