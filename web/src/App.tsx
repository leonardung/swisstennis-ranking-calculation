import { useEffect, useMemo, useRef, useState } from "react";
import { api, clearCache } from "./api";
import { useAsync, useRoute } from "./hooks";
import { I18nContext, loadLang, makeT, saveLang, type I18n, type Lang } from "./i18n";
import Header from "./components/Header";
import Home from "./components/Home";
import PlayerPage from "./components/PlayerPage";
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

  const route = useRoute();

  useEffect(() => {
    window.scrollTo({ top: 0 });
  }, [route.page, route.page === "player" ? route.id : 0]);

  return (
    <I18nContext.Provider value={i18n}>
      <Header meta={meta.data} />
      <main className="container">
        {meta.backendLoading && !meta.data ? (
          <BackendLoading />
        ) : route.page === "player" ? (
          <PlayerPage key={`${route.id}-${dataVersion}`} id={route.id} tab={route.tab} meta={meta.data} />
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
