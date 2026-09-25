import { navigate, playerHref } from "../hooks";
import { useI18n } from "../i18n";
import { fmtDate, fmtDateTime } from "../format";
import type { Meta } from "../types";
import SearchBox from "./SearchBox";

export default function Header({ meta }: { meta: Meta | null }) {
  const { t, lang, setLang, locale } = useI18n();
  const scrape = meta?.scrape;
  return (
    <header className="app-header">
      <div className="container header-inner">
        <a className="brand" href="#/">
          <span className="brand-ball" aria-hidden />
          <span className="brand-text">
            <strong>{t("appName")}</strong>
            <small>{t("appTagline")}</small>
          </span>
        </a>
        <SearchBox className="header-search" onSelect={(p) => navigate(playerHref(p.id))} />
        <div className="header-side">
          {meta && (
            <div className="freshness" title={freshnessTitle()}>
              <span className={`dot ${scrape?.running ? "dot-busy" : scrape?.last_error ? "dot-err" : "dot-ok"}`} />
              <span className="freshness-text">
                {scrape?.running ? (
                  t("scrapeRunning")
                ) : (
                  <>
                    <small>{t("dataUpdated")}</small>
                    <span>{fmtDateTime(meta.data_updated, locale)}</span>
                  </>
                )}
              </span>
            </div>
          )}
          <button
            className="btn btn-ghost lang-toggle"
            onClick={() => setLang(lang === "en" ? "fr" : "en")}
            title={t("langToggleTitle")}
            aria-label={t("langToggleTitle")}
          >
            {t("langToggle")}
          </button>
        </div>
      </div>
    </header>
  );

  function freshnessTitle(): string {
    if (!meta) return "";
    const lines = [
      `${t("dataUpdated")}: ${fmtDateTime(meta.data_updated, locale)}`,
      `${t("homeToday")}: ${fmtDate(meta.today, locale)}`,
    ];
    if (scrape?.last_run) lines.push(`${t("scrapeLast")}: ${fmtDateTime(scrape.last_run, locale)}`);
    if (scrape?.next_run) lines.push(`${t("scrapeNext")}: ${fmtDateTime(scrape.next_run, locale)}`);
    if (scrape?.last_error) lines.push(`${t("scrapeError")}: ${scrape.last_error}`);
    return lines.join("\n");
  }
}
