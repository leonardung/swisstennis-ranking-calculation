import { useMemo, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api";
import { trackEvent } from "../analytics";
import { useChartColors } from "../chartTheme";
import { playerHref, useAsync, useDebounced, useUrlState } from "../hooks";
import { useI18n, type MsgKey } from "../i18n";
import { CATEGORIES, fmt3, fmtChance, fmtDate, fmtDateShort, fmtInt, fmtIntDelta, signClass } from "../format";
import type { PlayerDetail, SimSide, SimState } from "../types";
import { AsyncStatus, Card, CategoryBadge, ClassChange, Delta, Hint, Spinner } from "./common";
import { MatchTable } from "./MatchListDialog";
import SearchBox from "./SearchBox";

const SUGGESTED = 6;

interface Opp {
  id: number;
  name: string;
  /** matches played against them (most played) or the last one (recent) */
  n?: number;
  lastDate?: string;
}

export default function SimulatorTab({ player }: { player: PlayerDetail }) {
  const { t, locale } = useI18n();
  const [oppParam, setOppParam] = useUrlState("opp", "");
  const oppId = Number(oppParam) || null;
  const [selfError, setSelfError] = useState(false);
  const stats = useAsync((s) => api.stats(player.id, "all", s), [player.id]);

  // the latest opponents, then the most played of the others
  const [recent, most] = useMemo(() => {
    const known = (stats.data?.opponents ?? []).filter((o) => o.id != null && o.id !== player.id);
    const byDate = [...known].sort((a, b) => b.last_date.localeCompare(a.last_date)).slice(0, SUGGESTED);
    const taken = new Set(byDate.map((o) => o.id));
    const byCount = known
      .filter((o) => !taken.has(o.id))
      .sort((a, b) => b.won + b.lost - (a.won + a.lost) || b.last_date.localeCompare(a.last_date))
      .slice(0, SUGGESTED);
    const opp = (o: (typeof known)[number]): Opp => ({ id: o.id!, name: o.name, n: o.won + o.lost, lastDate: o.last_date });
    return [byDate.map(opp), byCount.map(opp)];
  }, [stats.data, player.id]);

  const opponent = useAsync(oppId ? (s) => api.player(oppId, s) : null, [oppId]);
  const sim = useAsync(oppId ? (s) => api.simulate(player.id, oppId, s) : null, [player.id, oppId]);
  const opp = opponent.data?.id === oppId ? opponent.data : null;

  const pick = (o: { id: number; name: string }) => {
    if (o.id === player.id) {
      setSelfError(true);
      return;
    }
    setSelfError(false);
    setOppParam(String(o.id));
    trackEvent("simulate", { player: player.name, opponent: o.name });
  };

  const chips = (label: string, list: Opp[], title: (o: Opp) => string) =>
    list.length > 0 && (
      <div className="recent">
        <small className="muted">{label}</small>
        <div className="chips">
          {list.map((o) => (
            <button key={o.id} className={`chip ${oppId === o.id ? "active" : ""}`} onClick={() => pick(o)} title={title(o)}>
              {o.name}
            </button>
          ))}
        </div>
      </div>
    );

  return (
    <div className="simulator">
      <Card title={t("simTitle")}>
        <p className="muted">{t("simIntro")}</p>
        <SearchBox className="sim-search" placeholder={t("simSearch")} onSelect={pick} />
        {selfError && <p className="hint neg">{t("simSelf")}</p>}
        {chips(t("simRecent"), recent, (o) => fmtDate(o.lastDate, locale))}
        {chips(t("simMostPlayed"), most, (o) => t("matchCount", { n: o.n ?? 0 }))}
      </Card>

      {oppId && (
        <div className="sim-result">
          {(sim.loading && !sim.backendLoading) || (!opp && opponent.loading) ? (
            <Card>
              <Spinner label={t("simRunning")} />
            </Card>
          ) : sim.data && opp ? (
            <>
              {sim.data.chance != null && <ChanceCard chance={sim.data.chance} player={player.name} opponent={opp.name} />}
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
              <RankComparison player={player} opponent={opp} />
              <HeadToHead playerId={player.id} opponent={opp} />
            </>
          ) : (
            <AsyncStatus {...(sim.error ? sim : opponent)} />
          )}
        </div>
      )}

      <MoveUpCard playerId={player.id} />
      <PredictCard player={player} />
    </div>
  );
}

/** The player's and the opponent's chance of winning, as a split bar in the rank chart's colors. */
function ChanceCard({ chance, player, opponent }: { chance: number; player: string; opponent: string }) {
  const { t } = useI18n();
  const colors = useChartColors();
  return (
    <Card title={<Hint label={t("simChance")} text={t("simChanceHelp")} />}>
      <div className="chance-names">
        <span>
          {player} <strong>{fmtChance(chance)}</strong>
        </span>
        <span>
          <strong>{fmtChance(1 - chance)}</strong> {opponent}
        </span>
      </div>
      <div className="chance-bar" aria-hidden>
        <span style={{ width: `${chance * 100}%`, background: colors.s1 }} />
        <span style={{ background: colors.s2 }} />
      </div>
    </Card>
  );
}

interface RankPoint {
  date: string;
  ts: number;
  a: number | null;
  b: number | null;
  aClass: string | null;
  bClass: string | null;
}

/** Both players' rank on the lists, drawn like the history chart (logarithmic axis). */
function RankComparison({ player, opponent }: { player: PlayerDetail; opponent: PlayerDetail }) {
  const { t, locale } = useI18n();
  const colors = useChartColors();
  const points = useMemo(() => {
    const byDate = new Map<string, RankPoint>();
    const add = (p: PlayerDetail, side: "a" | "b") => {
      for (const h of p.history) {
        if (h.rank == null || h.rank <= 0) continue;
        const pt = byDate.get(h.date) ?? { date: h.date, ts: Date.parse(h.date), a: null, b: null, aClass: null, bClass: null };
        pt[side] = h.rank;
        pt[`${side}Class`] = h.class;
        byDate.set(h.date, pt);
      }
    };
    add(player, "a");
    add(opponent, "b");
    return [...byDate.values()].filter((p) => !Number.isNaN(p.ts)).sort((x, y) => x.ts - y.ts);
  }, [player, opponent]);
  if (points.length === 0) return null;
  const line = { type: "monotone" as const, strokeWidth: 2, dot: { r: 3 }, activeDot: { r: 5 }, connectNulls: true, isAnimationActive: false };
  return (
    <Card title={t("simRankComparison")}>
      <div className="chart-box">
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={points} margin={{ top: 10, right: 16, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={colors.grid} vertical={false} />
            <XAxis
              dataKey="ts"
              type="number"
              scale="time"
              domain={["dataMin", "dataMax"]}
              tickFormatter={(v: number) => fmtDateShort(new Date(v).toISOString().slice(0, 10), locale)}
              stroke={colors.axis}
              tick={{ fontSize: 12 }}
              tickLine={false}
              minTickGap={24}
            />
            <YAxis
              reversed
              scale="log"
              domain={["dataMin", "dataMax"]}
              allowDecimals={false}
              stroke={colors.axis}
              tick={{ fontSize: 12 }}
              tickLine={false}
              axisLine={false}
              width={56}
            />
            <Tooltip content={<RankTooltip names={[player.name, opponent.name]} />} />
            <Legend verticalAlign="top" height={28} iconType="plainline" itemSorter={null} />
            <Line dataKey="a" name={player.name} stroke={colors.s1} {...line} />
            <Line dataKey="b" name={opponent.name} stroke={colors.s2} {...line} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p className="hint small">{t("historyHelpRank")}</p>
    </Card>
  );
}

function RankTooltip({ active, payload, names }: { active?: boolean; payload?: { payload: RankPoint }[]; names: [string, string] }) {
  const { t, locale } = useI18n();
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  const row = (name: string, rank: number | null, cls: string | null) =>
    rank != null && (
      <div className="chart-tip-row">
        <CategoryBadge cls={cls} size="sm" /> {name}: <strong>{fmtInt(rank, locale)}</strong>
      </div>
    );
  return (
    <div className="chart-tip">
      <div className="chart-tip-head">
        {fmtDate(p.date, locale)} · {t("rank")}
      </div>
      {row(names[0], p.a, p.aClass)}
      {row(names[1], p.b, p.bClass)}
    </div>
  );
}

function HeadToHead({ playerId, opponent }: { playerId: number; opponent: PlayerDetail }) {
  const { t } = useI18n();
  const st = useAsync((s) => api.h2h(playerId, opponent.id, s), [playerId, opponent.id]);
  const won = st.data?.filter((m) => m.result === "win").length ?? 0;
  const lost = (st.data?.length ?? 0) - won;
  return (
    <Card
      title={t("h2h")}
      actions={
        st.data && st.data.length > 0 ? (
          <strong className="h2h-score">
            <span className="pos">{won}</span> – <span className="neg">{lost}</span>
          </strong>
        ) : undefined
      }
    >
      {!st.data ? (
        <AsyncStatus {...st} />
      ) : st.data.length === 0 ? (
        <p className="muted">{t("h2hNone", { name: opponent.name })}</p>
      ) : (
        <div className="table-wrap">
          <MatchTable matches={st.data} h2h />
        </div>
      )}
    </Card>
  );
}

const LEVELS: [number, MsgKey][] = [
  [25, "moveUpWeak"],
  [50, "moveUpMedium"],
  [75, "moveUpStrong"],
];

/** The wins the player needs to reach the next category, per opponent category and strength. */
function MoveUpCard({ playerId }: { playerId: number }) {
  const { t, locale } = useI18n();
  const st = useAsync((s) => api.moveUp(playerId, s), [playerId]);
  if (st.loading || st.error) return <AsyncStatus {...st} />;
  const d = st.data;
  const reachable = d?.levels.filter((l) => l.opponents.some((o) => o.wins != null)) ?? [];
  // stronger categories than the weakest one where a single win can be enough say nothing more
  const once = reachable.map((l) => l.opponents.some((o) => o.wins === 1)).lastIndexOf(true);
  const levels = reachable.slice(Math.max(once, 0));
  return (
    <Card title={t("moveUpTitle")}>
      {!d || levels.length === 0 ? (
        <p className="muted">{t("moveUpNone")}</p>
      ) : (
        <>
          <p className="muted">
            {t("moveUpIntro", { from: d.class, to: d.target, C: fmt3(d.C), need: fmt3(d.need) })}
          </p>
          <div className="table-wrap">
            <table className="data move-up">
              <thead>
                <tr>
                  <th>{t("moveUpAgainst")}</th>
                  {LEVELS.map(([q, label]) => (
                    <th key={q} className="num">
                      <Hint label={t(label)} text={t("moveUpLevelHelp", { p: q })} align="end" />
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {levels.map((l) => (
                  <tr key={l.class}>
                    <th>
                      <CategoryBadge cls={l.class} size="sm" />
                    </th>
                    {LEVELS.map(([q]) => {
                      const o = l.opponents.find((x) => x.percentile === q);
                      return (
                        <td key={q} className="num" title={o ? `W ${fmt3(o.W)}` : undefined}>
                          {o?.wins != null ? (
                            <strong>{t("moveUpWins", { n: o.wins })}</strong>
                          ) : (
                            <span className="muted">{`> ${fmtInt(d.max_wins, locale)}`}</span>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="hint small">{t("moveUpHelp")}</p>
        </>
      )}
    </Card>
  );
}

// custom prediction: a result is outcome:count:category:percentile (see the backend's predict)
const MAX_PER_ROW = 30;
const MAX_RESULTS = 60;

interface Hypo {
  win: boolean;
  count: number;
  cls: string;
  percentile: number;
}

function parseHypos(v: string): Hypo[] {
  return v
    .split(",")
    .map((x) => x.split(":"))
    .filter((p) => p.length === 4)
    .map(([o, n, cls, q]) => ({ win: o !== "loss", count: Number(n) || 1, cls, percentile: Number(q) || 50 }));
}

const formatHypos = (h: Hypo[]) => h.map((x) => `${x.win ? "win" : "loss"}:${x.count}:${x.cls}:${x.percentile}`).join(",");

/** The player's values after results the visitor makes up (kept in the URL). */
function PredictCard({ player }: { player: PlayerDetail }) {
  const { t } = useI18n();
  const own = player.today?.class && CATEGORIES.includes(player.today.class) ? player.today.class : "R5";
  const [param, setParam] = useUrlState("pred", "");
  const rows = param ? parseHypos(param) : [{ win: true, count: 1, cls: own, percentile: 50 }];
  const set = (h: Hypo[]) => setParam(formatHypos(h));
  const update = (i: number, change: Partial<Hypo>) => set(rows.map((r, j) => (j === i ? { ...r, ...change } : r)));
  const total = rows.reduce((n, r) => n + r.count, 0);
  const query = useDebounced(total <= MAX_RESULTS && rows.length ? formatHypos(rows) : "", 400);
  const st = useAsync(query ? (s) => api.predict(player.id, query, s) : null, [player.id, query]);
  const d = st.data;
  return (
    <Card title={t("predictTitle")}>
      <p className="muted">{t("predictIntro")}</p>
      <div className="predict-rows">
        {rows.map((r, i) => (
          <div key={i} className="predict-row">
            <select value={r.win ? "win" : "loss"} onChange={(e) => update(i, { win: e.target.value === "win" })} aria-label={t("result")}>
              <option value="win">{t("predictWins")}</option>
              <option value="loss">{t("predictLosses")}</option>
            </select>
            <input
              type="number"
              min={1}
              max={MAX_PER_ROW}
              value={r.count}
              aria-label={t("predictCount")}
              onChange={(e) => update(i, { count: Math.min(MAX_PER_ROW, Math.max(1, Math.round(Number(e.target.value)) || 1)) })}
            />
            <span className="muted">×</span>
            <select value={r.cls} onChange={(e) => update(i, { cls: e.target.value })} aria-label={t("category")}>
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
            <select value={r.percentile} onChange={(e) => update(i, { percentile: Number(e.target.value) })} aria-label={t("predictStrength")}>
              {LEVELS.map(([q, label]) => (
                <option key={q} value={q}>
                  {t(label)}
                </option>
              ))}
            </select>
            <button className="btn btn-ghost predict-remove" onClick={() => set(rows.filter((_, j) => j !== i))} aria-label={t("predictRemove")} title={t("predictRemove")}>
              ✕
            </button>
          </div>
        ))}
        <button className="btn btn-ghost predict-add" onClick={() => set([...rows, { ...(rows[rows.length - 1] ?? { win: true, count: 1, cls: own, percentile: 50 }) }])}>
          + {t("predictAdd")}
        </button>
      </div>
      {total > MAX_RESULTS && <p className="hint neg">{t("predictTooMany", { n: MAX_RESULTS })}</p>}
      {rows.length > 0 && total <= MAX_RESULTS && (
        <div className="predict-result">
          {d ? (
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
                      <CategoryBadge cls={d.before.class} size="sm" />
                    </td>
                    <td className="num">{fmt3(d.before.W)}</td>
                    <td className="num">{fmt3(d.before.C)}</td>
                    <td className="num">{fmtInt(d.before.rank)}</td>
                  </tr>
                  <SimRow
                    label={t("predictAfter")}
                    good={(d.after.C ?? 0) >= (d.before.C ?? 0)}
                    before={d.before}
                    after={d.after}
                  />
                </tbody>
              </table>
            </div>
          ) : (
            <AsyncStatus {...st} />
          )}
          {st.loading && d && <Spinner />}
        </div>
      )}
      <p className="hint small">{t("predictHelp")}</p>
    </Card>
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
