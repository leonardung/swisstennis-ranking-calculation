import { useMemo, useState } from "react";
import { api } from "../api";
import { playerHref, useAsync } from "../hooks";
import { useI18n } from "../i18n";
import { fmt3, fmtDate, fmtInt, fmtIntDelta, signClass } from "../format";
import type { PlayerDetail, SimSide, SimState } from "../types";
import { AsyncStatus, Card, CategoryBadge, ClassChange, Delta, Spinner } from "./common";
import SearchBox from "./SearchBox";

interface Opp {
  id: number;
  name: string;
  class: string | null;
  lastDate?: string;
}

export default function SimulatorTab({ player }: { player: PlayerDetail }) {
  const { t, locale } = useI18n();
  const [opp, setOpp] = useState<Opp | null>(null);
  const [selfError, setSelfError] = useState(false);
  const matches = useAsync((s) => api.matches(player.id, s), [player.id]);

  const recent = useMemo(() => {
    const seen = new Set<number>();
    const out: Opp[] = [];
    const ms = [...(matches.data?.matches ?? [])].sort((a, b) => b.date.localeCompare(a.date));
    for (const m of ms) {
      const id = m.opponent.id;
      if (id == null || seen.has(id) || id === player.id) continue;
      seen.add(id);
      out.push({ id, name: m.opponent.name, class: m.opponent.class, lastDate: m.date });
      if (out.length >= 12) break;
    }
    return out;
  }, [matches.data, player.id]);

  const sim = useAsync(opp ? (s) => api.simulate(player.id, opp.id, s) : null, [player.id, opp?.id]);

  const pick = (o: Opp) => {
    if (o.id === player.id) {
      setSelfError(true);
      return;
    }
    setSelfError(false);
    setOpp(o);
  };

  return (
    <div className="simulator">
      <Card title={t("simTitle")}>
        <p className="muted">{t("simIntro")}</p>
        <SearchBox
          className="sim-search"
          placeholder={t("simSearch")}
          onSelect={(p) => pick({ id: p.id, name: p.name, class: p.class_today ?? p.class_official })}
        />
        {selfError && <p className="hint neg">{t("simSelf")}</p>}
        {recent.length > 0 && (
          <div className="recent">
            <small className="muted">{t("simRecent")}</small>
            <div className="chips">
              {recent.map((o) => (
                <button key={o.id} className={`chip ${opp?.id === o.id ? "active" : ""}`} onClick={() => pick(o)} title={o.lastDate ? fmtDate(o.lastDate, locale) : undefined}>
                  <CategoryBadge cls={o.class} size="sm" />
                  {o.name}
                </button>
              ))}
            </div>
          </div>
        )}
      </Card>

      {opp && (
        <div className="sim-result">
          {sim.loading && !sim.backendLoading ? (
            <Card>
              <Spinner label={t("simRunning")} />
            </Card>
          ) : sim.data ? (
            <div className="sim-grid">
              <SimCard title={player.name} role={t("simPlayer")} side={sim.data.player} perspective="player" playerName={player.name} />
              <SimCard
                title={opp.name}
                href={playerHref(opp.id)}
                role={t("simOpponent")}
                side={sim.data.opponent}
                perspective="opponent"
                playerName={player.name}
              />
            </div>
          ) : (
            <AsyncStatus {...sim} />
          )}
        </div>
      )}
    </div>
  );
}

function SimCard({
  title,
  role,
  side,
  perspective,
  playerName,
  href,
}: {
  title: string;
  role: string;
  side: SimSide;
  perspective: "player" | "opponent";
  playerName: string;
  href?: string;
}) {
  const { t } = useI18n();
  const first = playerName.split(" ")[0];
  // "win"/"loss" are from the selected player's point of view.
  const rows: { key: "win" | "loss"; label: string; sub?: string; good: boolean }[] =
    perspective === "player"
      ? [
          { key: "win", label: t("simAfterWin"), good: true },
          { key: "loss", label: t("simAfterLoss"), good: false },
        ]
      : [
          { key: "win", label: t("simAfterLoss"), sub: t("simIfWins", { name: first }), good: false },
          { key: "loss", label: t("simAfterWin"), sub: t("simIfLoses", { name: first }), good: true },
        ];
  return (
    <Card
      className="sim-card"
      title={
        <span className="sim-title">
          <small className="muted">{role}</small>
          {href ? <a href={href}>{title}</a> : title}
        </span>
      }
      actions={<CategoryBadge cls={side.before.class} size="lg" />}
    >
      <div className="table-wrap">
        <table className="data sim-table">
          <thead>
            <tr>
              <th />
              <th>{t("category")}</th>
              <th className="num">W</th>
              <th className="num">C</th>
              <th className="num">{t("rank")}</th>
            </tr>
          </thead>
          <tbody>
            <tr className="sim-before">
              <th>{t("simBefore")}</th>
              <td>
                <CategoryBadge cls={side.before.class} size="sm" />
              </td>
              <td className="num">{fmt3(side.before.W)}</td>
              <td className="num">{fmt3(side.before.C)}</td>
              <td className="num">{fmtInt(side.before.rank)}</td>
            </tr>
            {rows.map((r) => (
              <SimRow key={r.key} label={r.label} sub={r.sub} good={r.good} before={side.before} after={side[r.key]} />
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function SimRow({ label, sub, good, before, after }: { label: string; sub?: string; good: boolean; before: SimState; after: SimState }) {
  const dW = before.W != null && after.W != null ? after.W - before.W : null;
  const dC = before.C != null && after.C != null ? after.C - before.C : null;
  const dRank = before.rank != null && after.rank != null ? before.rank - after.rank : null;
  return (
    <tr className={good ? "row-win" : "row-loss"}>
      <th>
        <span className={`sim-label ${good ? "pos" : "neg"}`}>{label}</span>
        {sub && <small className="muted">{sub}</small>}
      </th>
      <td>
        <ClassChange from={before.class} to={after.class} />
      </td>
      <td className="num">
        {fmt3(after.W)}
        <Delta value={dW} />
      </td>
      <td className="num strong">
        {fmt3(after.C)}
        <Delta value={dC} />
      </td>
      <td className="num">
        {fmtInt(after.rank)}
        <span className={`delta ${signClass(dRank)}`}>{fmtIntDelta(dRank)}</span>
      </td>
    </tr>
  );
}
