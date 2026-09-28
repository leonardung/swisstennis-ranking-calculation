import { Fragment, useMemo, useState, type ReactNode } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api";
import { useChartColors } from "../chartTheme";
import { playerHref, useAsync } from "../hooks";
import { useI18n, type MsgKey } from "../i18n";
import { fmt3, fmtDate, fmtMonth, fmtPct, pctValue } from "../format";
import type { NotableMatch, Stats, WL } from "../types";
import { AsyncStatus, Card, CategoryBadge, ResultPill, Spinner } from "./common";
import { typeLabel } from "./MatchesTab";

export default function StatsTab({ playerId }: { playerId: number }) {
  const { t } = useI18n();
  const [range, setRange] = useState("window");
  const st = useAsync((s) => api.stats(playerId, range, s), [playerId, range]);
  const [years, setYears] = useState<number[]>([]);
  if (st.data && st.data.years.join() !== years.join()) setYears(st.data.years);

  const selector = (
    <label className="range-select">
      <span>{t("range")}</span>
      <select value={range} onChange={(e) => setRange(e.target.value)}>
        <option value="window">{t("rangeWindow")}</option>
        <option value="all">{t("rangeAll")}</option>
        {years.map((y) => (
          <option key={y} value={String(y)}>
            {y}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <div className="stats">
      <div className="stats-toolbar">
        <h2>{t("statsTitle")}</h2>
        {selector}
        {st.loading && st.data && <Spinner />}
      </div>
      {st.data ? <StatsBody s={st.data} playerId={playerId} /> : <AsyncStatus {...st} />}
    </div>
  );
}

function StatsBody({ s, playerId }: { s: Stats; playerId: number }) {
  const { t } = useI18n();
  const m = s.matches;
  return (
    <>
      <div className="stat-grid">
        <StatCard title={t("matchesStat")} won={m.won} lost={m.lost} big />
        <StatCard title={t("setsStat")} won={s.sets.won} lost={s.sets.lost} big />
        <StatCard title={t("gamesStat")} won={s.games.won} lost={s.games.lost} big />
        <StreakCard s={s} />
        <StatCard title={t("twoSets")} {...s.two_sets} />
        <StatCard title={t("threeSets")} {...s.three_sets} />
        <StatCard title={t("decidingSet")} {...s.deciding_set} />
        <StatCard title={t("matchTiebreak")} {...s.match_tiebreak} />
        <StatCard title={t("tiebreaks")} {...s.tiebreaks} />
        <StatCard title={t("sets75")} {...s.sets_7_5} />
        <StatCard
          title={t("afterWonFirst")}
          won={s.after_first_set.won_first.won}
          lost={s.after_first_set.won_first.played - s.after_first_set.won_first.won}
        />
        <StatCard
          title={t("afterLostFirst")}
          won={s.after_first_set.lost_first.won}
          lost={s.after_first_set.lost_first.played - s.after_first_set.lost_first.won}
        />
        <StatCard title={t("bagels")} won={s.bagels.given} lost={s.bagels.received} labels={[t("given"), t("received")]} />
        <StatCard title={t("breadsticks")} won={s.breadsticks.given} lost={s.breadsticks.received} labels={[t("given"), t("received")]} />
        <StatCard title={t("walkoversStat")} won={m.walkovers_won} lost={m.walkovers_lost} />
        <StatCard title={t("retiredStat")} won={m.retired_won} lost={m.retired_lost} />
      </div>

      <div className="grid-2">
        <Card title={t("byRelation")}>
          <WLTable
            rows={s.by_relation.map((r) => ({ key: r.relation, label: t(`rel_${r.relation}` as MsgKey), ...r }))}
          />
        </Card>
        <Card title={t("byType")}>
          <WLTable rows={s.by_type.map((r) => ({ key: r.type, label: typeLabel(r.type, t), ...r }))} />
        </Card>
      </div>

      <TimeChart s={s} />

      <div className="grid-2">
        <Card title={t("byClass")}>
          <WLTable
            rows={s.by_class.map((r) => ({ key: r.class ?? "none", label: <CategoryBadge cls={r.class} size="sm" />, ...r }))}
          />
        </Card>
        <div className="stack">
          <NotableList title={t("bestWins")} items={s.best_wins} kind="win" />
          <NotableList title={t("worstLosses")} items={s.worst_losses} kind="loss" />
        </div>
      </div>

      <OpponentsTable s={s} playerId={playerId} />
    </>
  );
}

function WLBar({ won, lost }: { won: number; lost: number }) {
  const total = won + lost;
  const w = total ? (won / total) * 100 : 0;
  return (
    <div className="wl-bar" aria-hidden>
      {total > 0 && (
        <>
          <span className="wl-won" style={{ width: `${w}%` }} />
          <span className="wl-lost" style={{ width: `${100 - w}%` }} />
        </>
      )}
    </div>
  );
}

function StatCard({ title, won, lost, big, labels }: { title: string; won: number; lost: number; big?: boolean; labels?: [string, string] }) {
  const { t } = useI18n();
  return (
    <div className={`stat-card ${big ? "stat-big" : ""}`}>
      <div className="stat-title">{title}</div>
      <div className="stat-main">
        <span className="stat-value">
          <span className="pos">{won}</span>
          <span className="muted">–</span>
          <span className="neg">{lost}</span>
        </span>
        <span className="stat-pct">{fmtPct(won, won + lost)}</span>
      </div>
      <WLBar won={won} lost={lost} />
      <div className="stat-legend muted">
        <span>{labels?.[0] ?? t("won")}</span>
        <span>{labels?.[1] ?? t("lost")}</span>
      </div>
    </div>
  );
}

function StreakCard({ s }: { s: Stats }) {
  const { t } = useI18n();
  const cur = s.streak;
  return (
    <div className="stat-card stat-big">
      <div className="stat-title">{t("streaks")}</div>
      <div className="stat-main">
        <span className={`stat-value ${cur.current_type === "win" ? "pos" : cur.current_type === "loss" ? "neg" : ""}`}>
          {cur.current_type ? `${cur.current} ${cur.current_type === "win" ? "W" : "L"}` : "–"}
        </span>
        <span className="stat-pct muted">{t("currentStreak")}</span>
      </div>
      <div className="streak-rows">
        <span>
          {t("longestWin")}: <strong className="pos">{s.streak.longest_win}</strong>
        </span>
        <span>
          {t("longestLoss")}: <strong className="neg">{s.streak.longest_loss}</strong>
        </span>
      </div>
    </div>
  );
}

function WLTable({ rows }: { rows: (WL & { key: string; label: ReactNode })[] }) {
  const { t } = useI18n();
  if (rows.length === 0) return <p className="muted">{t("noData")}</p>;
  return (
    <div className="table-wrap">
      <table className="data wl-table">
        <thead>
          <tr>
            <th />
            <th className="num">{t("won")}</th>
            <th className="num">{t("lost")}</th>
            <th className="num">{t("winPct")}</th>
            <th className="bar-col" />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key}>
              <th>{r.label}</th>
              <td className="num pos">{r.won}</td>
              <td className="num neg">{r.lost}</td>
              <td className="num">{fmtPct(r.won, r.won + r.lost)}</td>
              <td className="bar-col">
                <WLBar won={r.won} lost={r.lost} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TimeChart({ s }: { s: Stats }) {
  const { t, locale } = useI18n();
  const colors = useChartColors();
  const [mode, setMode] = useState<"month" | "year">(s.by_month.length > 1 ? "month" : "year");
  const data = useMemo(
    () =>
      mode === "month"
        ? [...s.by_month].sort((a, b) => a.month.localeCompare(b.month)).map((r) => ({ label: fmtMonth(r.month, locale), won: r.won, lost: r.lost }))
        : [...s.by_year].sort((a, b) => a.year - b.year).map((r) => ({ label: String(r.year), won: r.won, lost: r.lost })),
    [mode, s, locale],
  );
  return (
    <Card
      title={mode === "month" ? t("byMonth") : t("byYear")}
      actions={
        <div className="seg" role="group">
          <button className={mode === "month" ? "active" : ""} onClick={() => setMode("month")}>
            {t("byMonth")}
          </button>
          <button className={mode === "year" ? "active" : ""} onClick={() => setMode("year")}>
            {t("byYear")}
          </button>
        </div>
      }
    >
      {data.length === 0 ? (
        <p className="muted">{t("noData")}</p>
      ) : (
        <div className="chart-box">
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -16 }} barCategoryGap="20%">
              <CartesianGrid stroke={colors.grid} vertical={false} />
              <XAxis dataKey="label" stroke={colors.axis} tick={{ fontSize: 12 }} tickLine={false} minTickGap={8} />
              <YAxis stroke={colors.axis} tick={{ fontSize: 12 }} tickLine={false} axisLine={false} allowDecimals={false} />
              <Tooltip
                cursor={{ fill: colors.grid, opacity: 0.5 }}
                contentStyle={{ background: colors.surface, border: `1px solid ${colors.grid}`, borderRadius: 8, color: colors.text }}
                formatter={(v, name) => [String(v), name === "won" ? t("won") : t("lost")]}
              />
              <Legend verticalAlign="top" height={28} itemSorter={null} formatter={(v: string) => (v === "won" ? t("won") : t("lost"))} />
              <Bar dataKey="won" stackId="a" fill={colors.win} stroke={colors.surface} strokeWidth={1} isAnimationActive={false} />
              <Bar dataKey="lost" stackId="a" fill={colors.loss} stroke={colors.surface} strokeWidth={1} radius={[4, 4, 0, 0]} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </Card>
  );
}

function NotableList({ title, items, kind }: { title: string; items: NotableMatch[]; kind: "win" | "loss" }) {
  const { t, locale } = useI18n();
  return (
    <Card title={title}>
      {items.length === 0 ? (
        <p className="muted">{t("noData")}</p>
      ) : (
        <ul className="notable">
          {items.map((it, i) => (
            <li key={i}>
              <CategoryBadge cls={it.opponent.class} size="sm" />
              <div className="notable-main">
                {it.opponent.id != null ? <a href={playerHref(it.opponent.id)}>{it.opponent.name}</a> : <span>{it.opponent.name}</span>}
                <small className="muted">
                  {fmtDate(it.date, locale)}{it.tournament && ` · ${it.tournament}`}
                </small>
              </div>
              <div className="notable-side">
                <span className={`mono ${kind === "win" ? "pos" : "neg"}`}>{it.score}</span>
                <small className="muted">{fmt3(it.opponent_value)}</small>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

type OppSort = "total" | "won" | "lost" | "pct" | "last" | "name";

function OpponentsTable({ s, playerId }: { s: Stats; playerId: number }) {
  const { t, locale } = useI18n();
  const [filter, setFilter] = useState("");
  const [sort, setSort] = useState<{ key: OppSort; desc: boolean }>({ key: "total", desc: true });
  const [expanded, setExpanded] = useState<number | null>(null);
  const [showAll, setShowAll] = useState(false);
  const LIMIT = 25;

  const rows = useMemo(() => {
    const q = filter.trim().toLowerCase();
    const f = s.opponents.filter((o) => !q || o.name.toLowerCase().includes(q));
    const val = (o: (typeof f)[number]): number | string => {
      switch (sort.key) {
        case "won":
          return o.won;
        case "lost":
          return o.lost;
        case "pct":
          return pctValue(o.won, o.won + o.lost) ?? -1;
        case "last":
          return o.last_date;
        case "name":
          return o.name.toLowerCase();
        default:
          return o.won + o.lost;
      }
    };
    const dir = sort.desc ? -1 : 1;
    return [...f].sort((a, b) => {
      const va = val(a), vb = val(b);
      const c = typeof va === "number" && typeof vb === "number" ? va - vb : String(va).localeCompare(String(vb));
      return c * dir || b.last_date.localeCompare(a.last_date);
    });
  }, [s.opponents, filter, sort]);

  const visible = showAll || filter ? rows : rows.slice(0, LIMIT);
  const th = (key: OppSort, label: string, cls = "") => (
    <th
      className={`sortable ${cls}`}
      onClick={() => setSort((p) => (p.key === key ? { key, desc: !p.desc } : { key, desc: key !== "name" }))}
    >
      {label}
      {sort.key === key ? (sort.desc ? " ▼" : " ▲") : ""}
    </th>
  );

  return (
    <Card
      title={`${t("h2h")} (${s.opponents.length})`}
      actions={
        <input className="input-sm" type="search" placeholder={t("filterOpponents")} value={filter} onChange={(e) => setFilter(e.target.value)} />
      }
    >
      <div className="table-wrap">
        <table className="data opp-table">
          <thead>
            <tr>
              {th("name", t("opponent"))}
              {th("total", t("played"), "num")}
              {th("won", t("won"), "num")}
              {th("lost", t("lost"), "num")}
              {th("pct", t("winPct"), "num")}
              {th("last", t("lastMatch"))}
            </tr>
          </thead>
          <tbody>
            {visible.length === 0 && (
              <tr>
                <td colSpan={6} className="empty">
                  {t("noData")}
                </td>
              </tr>
            )}
            {visible.map((o, i) => {
              const key = o.id ?? -(i + 1);
              const open = expanded === key;
              return (
                <Fragment key={`${key}-${o.name}`}>
                  <tr
                    className={`clickable ${open ? "open" : ""}`}
                    onClick={() => o.id != null && setExpanded(open ? null : key)}
                    aria-expanded={o.id != null ? open : undefined}
                  >
                    <td className="nowrap">
                      {o.id != null && <span className="caret">{open ? "▾" : "▸"}</span>}
                      {o.id != null ? (
                        <a href={playerHref(o.id)} onClick={(e) => e.stopPropagation()}>
                          {o.name}
                        </a>
                      ) : (
                        o.name
                      )}
                    </td>
                    <td className="num">{o.won + o.lost}</td>
                    <td className="num pos">{o.won}</td>
                    <td className="num neg">{o.lost}</td>
                    <td className="num">{fmtPct(o.won, o.won + o.lost)}</td>
                    <td className="nowrap">{fmtDate(o.last_date, locale)}</td>
                  </tr>
                  {open && o.id != null && (
                    <tr className="drill">
                      <td colSpan={6}>
                        <H2H playerId={playerId} opponentId={o.id} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      {!filter && rows.length > LIMIT && (
        <button className="btn btn-ghost show-more" onClick={() => setShowAll((v) => !v)}>
          {showAll ? t("showLess") : t("showAll", { n: rows.length })}
        </button>
      )}
    </Card>
  );
}

function H2H({ playerId, opponentId }: { playerId: number; opponentId: number }) {
  const { t, locale } = useI18n();
  const st = useAsync((sig) => api.h2h(playerId, opponentId, sig), [playerId, opponentId]);
  if (!st.data) return <AsyncStatus {...st} />;
  if (st.data.length === 0) return <p className="muted">{t("noMatches")}</p>;
  return (
    <table className="data h2h-table">
      <tbody>
        {[...st.data]
          .sort((a, b) => b.date.localeCompare(a.date))
          .map((m, i) => (
            <tr key={i}>
              <td className="nowrap">{fmtDate(m.date, locale)}</td>
              <td>{m.tournament}</td>
              <td className="mono nowrap">{m.score || "–"}</td>
              <td>
                <ResultPill result={m.result} how={m.how} />
              </td>
            </tr>
          ))}
      </tbody>
    </table>
  );
}
