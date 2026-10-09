import { useEffect, useMemo, useRef, useState } from "react";
import { api, clearCache } from "./api";
import { trackEvent, trackView } from "./analytics";
import { useAsync, useRoute, type Route } from "./hooks";
import { startPageTiming } from "./perf";
import { I18nContext, loadLang, makeT, saveLang, type I18n, type Lang } from "./i18n";
import Header from "./components/Header";
import MovedBanner from "./components/MovedBanner";
import Home from "./components/Home";
import PlayerPage from "./components/PlayerPage";
import TournamentsPage from "./components/TournamentsPage";
import TournamentPage from "./components/TournamentPage";
import PerfPage from "./components/PerfPage";
import { BackendLoading } from "./components/common";

export default function App() {
  const [lang, setLangState] = useState<Lang>(loadLang);
  const i18n = useMemo<I18n>(
    () => ({
      lang,
      locale: lang === "fr" ? "fr-CH" : "en-GB",
      t: makeT(lang),
      setLang: (l) => {
        saveLang(l);
        trackEvent("language", { lang: l });
        setLangState(l);
      },
    }),
    [lang],
  );

  useEffect(() => {
    document.documentElement.lang = lang;
    document.title = i18n.t("appName");
  }, [lang, i18n]);

  const meta = useAsync((s) => api.meta(s), []);
  const { reload: reloadMeta } = meta;

  // Refresh freshness info every minute; drop cached responses when the data changes.
  useEffect(() => {
    const id = window.setInterval(reloadMeta, 60_000);
    return () => window.clearInterval(id);
  }, [reloadMeta]);
  const lastUpdated = useRef<string | null | undefined>(undefined);
  const [dataVersion, setDataVersion] = useState(0);
  useEffect(() => {
    const u = meta.data?.data_updated;
    if (u === undefined) return;
    if (lastUpdated.current !== undefined && lastUpdated.current !== u) {
      clearCache();
      setDataVersion((v) => v + 1);
    }
    lastUpdated.current = u;
  }, [meta.data?.data_updated]);
  // the tournament tables are refreshed on their own schedule (every few hours): only the
  // tournament pages start over, an open player page (and its outlook) keeps its state
  const lastTournaments = useRef<string | null | undefined>(undefined);
  const [tournamentsVersion, setTournamentsVersion] = useState(0);
  useEffect(() => {
    const u = meta.data?.tournaments?.updated;
    if (u === undefined) return;
    if (lastTournaments.current !== undefined && lastTournaments.current !== u) {
      clearCache();
      setTournamentsVersion((v) => v + 1);
    }
    lastTournaments.current = u;
  }, [meta.data?.tournaments?.updated]);

  const route = useRoute();
  // each navigation counts, even to the same page (another query: a fresh filter state)
  // t0: when the page started (0 = the page load), for its loading time
  const nav = useRef({ route, n: 0, t0: 0 });
  if (nav.current.route !== route) nav.current = { route, n: nav.current.n + 1, t0: performance.now() };

  useEffect(() => {
    if (route.page !== "perf") startPageTiming(perfPage(route), nav.current.n ? "navigation" : "initial", nav.current.t0);
  }, [route]);

  useEffect(() => {
    if (route.page === "home") {
      document.title = i18n.t("appName");
      trackView();
    }
  }, [route.page]);

  return (
    <I18nContext.Provider value={i18n}>
      <Header
        meta={meta.data}
        showSearch={route.page !== "home"}
        inTournaments={route.page === "tournaments" || route.page === "tournament"}
      />
      <MovedBanner />
      <main className="container">
        {meta.backendLoading && !meta.data ? (
          <BackendLoading />
        ) : route.page === "player" ? (
          <PlayerPage key={`${route.id}-${dataVersion}`} id={route.id} tab={route.tab} />
        ) : route.page === "tournaments" ? (
          <TournamentsPage key={`${nav.current.n}-${dataVersion}-${tournamentsVersion}`} />
        ) : route.page === "perf" ? (
          <PerfPage />
        ) : route.page === "tournament" ? (
          <TournamentPage key={`${route.id}-${dataVersion}-${tournamentsVersion}`} id={route.id} eventId={route.eventId} />
        ) : (
          <Home meta={meta.data} />
        )}
      </main>
      <footer className="container footer muted">
        <div>{i18n.t("appName")} · DCL · C = W + R</div>
        <div>{i18n.t("footerNote")}</div>
      </footer>
    </I18nContext.Provider>
  );
}

/** The page of a route in the loading times: the player pages by tab, no ids. */
function perfPage(route: Route): string {
  return route.page === "player" ? `player/${route.tab}` : route.page;
}
