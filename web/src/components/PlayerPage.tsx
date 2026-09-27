import { useEffect } from "react";
import { api } from "../api";
import { trackView } from "../analytics";
import { playerHref, TABS, useAsync, type Tab } from "../hooks";
import { useI18n, type MsgKey } from "../i18n";
import { fmt3, fmtInt } from "../format";
import { AsyncStatus, CategoryBadge } from "./common";
import OverviewTab from "./OverviewTab";
import MatchesTab from "./MatchesTab";
import SimulatorTab from "./SimulatorTab";
import StatsTab from "./StatsTab";

const TAB_LABEL: Record<Tab, MsgKey> = {
  overview: "tabOverview",
  matches: "tabMatches",
  simulator: "tabSimulator",
  stats: "tabStats",
};

export default function PlayerPage({ id, tab }: { id: number; tab: Tab }) {
  const { t, locale } = useI18n();
  const st = useAsync((s) => api.player(id, s), [id]);
  const p = st.data;

  useEffect(() => {
    if (p) document.title = `${p.name} · ${t("appName")}`;
  }, [p, t]);

  useEffect(() => {
    if (p) trackView();
  }, [p, tab]);

  if (!p) {
    return (
      <AsyncStatus
        {...st}
        notFound={
          <div className="state-screen small">
            <h2>{t("notFound")}</h2>
            <a className="btn" href="#/">
              {t("backHome")}
            </a>
          </div>
        }
      />
    );
  }

  return (
    <div className="player-page">
      <section className="player-head">
        <div className="player-id">
          <h1>
            {p.name}
            <span className={`gender g-${p.gender}`} title={p.gender === "F" ? t("women") : t("men")}>
              {p.gender === "F" ? "♀" : "♂"}
            </span>
          </h1>
          <div className="muted player-sub">
            {p.licence && (
              <span>
                {t("licence")} <span className="mono">{p.licence}</span>
              </span>
            )}
            <span>{p.gender === "F" ? t("women") : t("men")}</span>
          </div>
        </div>
        <div className="player-quick">
          <div className="quick">
            <small>{t("official")}</small>
            <CategoryBadge cls={p.official?.class} size="lg" />
            <span className="muted">
              #{fmtInt(p.official?.rank, locale)} · C {fmt3(p.official?.C)}
            </span>
          </div>
          <span className="quick-arrow" aria-hidden>
            →
          </span>
          <div className="quick">
            <small>{t("today")}</small>
            <CategoryBadge cls={p.today?.class} size="lg" />
            <span className="muted">
              #{fmtInt(p.today?.rank, locale)} · C {fmt3(p.today?.C)}
            </span>
          </div>
        </div>
      </section>

      <nav className="tabs" role="tablist">
        {TABS.map((k) => (
          <a key={k} role="tab" aria-selected={tab === k} className={tab === k ? "tab active" : "tab"} href={playerHref(id, k)}>
            {t(TAB_LABEL[k])}
          </a>
        ))}
      </nav>

      <div className="tab-body">
        {tab === "overview" && <OverviewTab player={p} />}
        {tab === "matches" && <MatchesTab playerId={id} />}
        {tab === "simulator" && <SimulatorTab player={p} />}
        {tab === "stats" && <StatsTab playerId={id} />}
      </div>
    </div>
  );
}
