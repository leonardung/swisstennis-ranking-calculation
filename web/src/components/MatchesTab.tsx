import { useMemo, useState } from "react";
import { api } from "../api";
import { playerHref, useAsync, useUrlState } from "../hooks";
import { useI18n, type MsgKey } from "../i18n";
import { fmt3, fmtChance, fmtDate, fmtDateShort } from "../format";
import type { Match, Season } from "../types";
import ProgressChart from "./ProgressChart";
import { AsyncStatus, Card, CategoryBadge, ClassChange, Delta, Hint, ResultPill, Standing } from "./common";

type Filter = "all" | "wins" | "losses" | "not_counted" | "dropping";
type SortKey = "date" | "delta";

// the filters of a past season (nothing counts any more)
const PAST_FILTERS: Filter[] = ["all", "wins", "losses"];
const FILTERS: [Filter, MsgKey][] = [
  ["all", "filterAll"],
  ["wins", "filterWins"],
  ["losses", "filterLosses"],
  ["not_counted", "filterNotCounted"],
  ["dropping", "filterDropping"],
];

export function typeLabel(type: string, t: (k: MsgKey) => string): string {
  const key = `type_${type}` as MsgKey;
  const s = t(key);
  return s === key ? type : s;
}

/** "Oct 2024 – Sep 2025": the months of a season (its end is the day after). */
function seasonLabel(s: Season, locale: string): string {
  const last = new Date(Date.parse(s.end) - 86_400_000).toISOString().slice(0, 10);
  return `${fmtDateShort(s.start, locale)} – ${fmtDateShort(last, locale)}`;
}

export default function MatchesTab({ playerId }: { playerId: number }) {
  const { t, locale } = useI18n();
  const [seasonParam, setSeason] = useUrlState("season", "0");
  const season = Number(seasonParam) || 0;
  const st = useAsync((s) => api.matches(playerId, season, s), [playerId, season]);
  const [seasons, setSeasons] = useState<Season[]>([]);
  if (st.data && st.data.seasons.length !== seasons.length) setSeasons(st.data.seasons);
  const [filterParam, setFilter] = useUrlState("filter", "all");
  const [sortParam, setSortParam] = useUrlState("sort", "date-desc");
  const past = season > 0;
  const filter = (past && !PAST_FILTERS.includes(filterParam as Filter) ? "all" : filterParam) as Filter;
  const [sortKey, sortDir] = sortParam.split("-");
  const sort = { key: (sortKey === "delta" && !past ? "delta" : "date") as SortKey, desc: sortDir !== "asc" };
  const setSort = (f: (s: { key: SortKey; desc: boolean }) => { key: SortKey; desc: boolean }) => {
    const n = f(sort);
    setSortParam(`${n.key}-${n.desc ? "desc" : "asc"}`);
  };

  const all = st.data?.season === season ? st.data.matches : [];

  const summary = useMemo(() => {
    let counted = 0,
      discarded = 0;
    for (const m of all) {
      if (m.counted) counted++;
      if (m.reason === "discarded_loss") discarded++;
    }
    return { counted, discarded };
  }, [all]);

  const counts = useMemo(() => {
    const c: Record<Filter, number> = { all: all.length, wins: 0, losses: 0, not_counted: 0, dropping: 0 };
    for (const m of all) {
      if (m.result === "win") c.wins++;
      else c.losses++;
      if (!m.counted) c.not_counted++;
      if (m.drops_after_next_official) c.dropping++;
    }
    return c;
  }, [all]);

  const rows = useMemo(() => {
    const f = all.filter((m) => {
      switch (filter) {
        case "wins":
          return m.result === "win";
        case "losses":
          return m.result === "loss";
        case "not_counted":
          return !m.counted;
        case "dropping":
          return m.drops_after_next_official;
        default:
          return true;
      }
    });
    const dir = sort.desc ? -1 : 1;
    return [...f].sort((a, b) => {
      if (sort.key === "delta") {
        // uncounted matches (null delta) always at the bottom
        const av = a.delta_C, bv = b.delta_C;
        if (av == null && bv == null) return b.date.localeCompare(a.date);
        if (av == null) return 1;
        if (bv == null) return -1;
        return (av - bv) * dir || b.date.localeCompare(a.date);
      }
      return a.date.localeCompare(b.date) * dir || (a.id - b.id) * dir;
    });
  }, [all, filter, sort.key, sort.desc]);

  if (!st.data && !seasons.length) return <AsyncStatus {...st} />;

  const toggleSort = (key: SortKey) =>
    setSort((s) => (s.key === key ? { key, desc: !s.desc } : { key, desc: true }));
  const sortMark = (key: SortKey) => (sort.key === key ? (sort.desc ? " ▼" : " ▲") : "");

  return (
    <Card
      title={t(past ? "matchesTitlePast" : "matchesTitle")}
      actions={
        <div className="range-select" aria-busy={st.loading}>
          <span id="season-label">{t("season")}</span>
          {/* seasons go from today's window (0) back in time */}
          <button
            className="btn btn-ghost season-step"
            onClick={() => setSeason(String(season + 1))}
            disabled={season >= seasons.length - 1}
            aria-label={t("seasonBefore")}
            title={t("seasonBefore")}
          >
            ‹
          </button>
          <select aria-labelledby="season-label" value={season} onChange={(e) => setSeason(e.target.value)}>
            {seasons.map((s, i) => (
              <option key={s.start} value={i}>
                {i === 0 ? t("seasonCurrent", { months: seasonLabel(s, locale) }) : seasonLabel(s, locale)}
              </option>
            ))}
          </select>
          <button
            className="btn btn-ghost season-step"
            onClick={() => setSeason(String(season - 1))}
            disabled={season <= 0}
            aria-label={t("seasonAfter")}
            title={t("seasonAfter")}
          >
            ›
          </button>
          {/* its place is kept while idle: the controls do not move */}
          <span className={`spinner season-spinner ${st.loading ? "" : "idle"}`} aria-hidden />
        </div>
      }
    >
      {st.data && st.data.season === season && (
        <p className="muted small">
          {t("windowFromTo", { start: fmtDate(st.data.window.start, locale), end: fmtDate(st.data.window.end, locale) })}
        </p>
      )}
      {!st.data && <AsyncStatus {...st} />}
      {st.data?.season === season && st.data.progress && <ProgressChart progress={st.data.progress} matches={st.data.matches} />}
      {!past && (
      <div className="summary-bar">
        <div className="summary-item">
          <strong>{summary.counted}</strong>
          <span>{t("summaryCounted")}</span>
        </div>
        <div className="summary-item">
          <strong>{summary.discarded}</strong>
          <span>{t("summaryDiscarded")}</span>
        </div>
      </div>
      )}

      <div className="toolbar">
        <div className="seg seg-filters" role="group">
          {FILTERS.filter(([k]) => !past || PAST_FILTERS.includes(k)).map(([k, label]) => (
            <button key={k} className={filter === k ? "active" : ""} onClick={() => setFilter(k)}>
              {t(label)} <span className="count">{counts[k]}</span>
            </button>
          ))}
        </div>
        <div className="seg seg-sort" role="group" aria-label={t("sortBy")}>
          <button className={sort.key === "date" ? "active" : ""} onClick={() => toggleSort("date")}>
            {t("sortDate")}
            {sortMark("date")}
          </button>
          {!past && (
            <button className={sort.key === "delta" ? "active" : ""} onClick={() => toggleSort("delta")}>
              {t("sortDelta")}
              {sortMark("delta")}
            </button>
          )}
        </div>
      </div>

      <div className="table-wrap matches-wrap">
        <table className="data matches">
          <thead>
            <tr>
              <th className="sortable" onClick={() => toggleSort("date")}>
                {t("date")}
                {sortMark("date")}
              </th>
              <th>{t("tournament")}</th>
              <th>
                <Hint label={t("opponent")} text={t(past ? "opponentThenHelp" : "opponentNowHelp")} />
              </th>
              {/* today's window: always the official list in force, not worth a column */}
              {past && (
                <th>
                  <Hint label={t("youThen")} text={t("youThenHelp")} />
                </th>
              )}
              {!past && (
                <th className="num">
                  <Hint label={t("oppValue")} text={t("oppValueHelp")} />
                </th>
              )}
              <th>{t("score")}</th>
              <th>
                <Hint label={t("result")} text={t("resultChanceHelp")} />
              </th>
              {!past && (
                <>
                  <th className="num">
                    <Hint label="Δ W" text={t("deltaWHelp")} align="end" />
                  </th>
                  <th className="num sortable" onClick={() => toggleSort("delta")}>
                    <Hint label="Δ C" text={t("deltaCHelp")} align="end" />
                    {sortMark("delta")}
                  </th>
                  <th>{t("status")}</th>
                </>
              )}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={past ? 6 : 9} className="empty">
                  {t("noMatches")}
                </td>
              </tr>
            )}
            {rows.map((m) => (
              <MatchRow key={m.id} m={m} past={past} />
            ))}
          </tbody>
        </table>
      </div>
      {!past && <p className="hint small">{t("deltaHelp")}</p>}
    </Card>
  );
}

function MatchRow({ m, past }: { m: Match; past: boolean }) {
  const { t, locale } = useI18n();
  const o = m.opponent;
  return (
    <tr className={`${m.counted || past ? "" : "not-counted"} ${m.result === "win" ? "row-win" : "row-loss"}`}>
      <td className="m-date nowrap">{fmtDate(m.date, locale)}</td>
      <td className="m-tourn tourn">
        <span title={m.tournament ?? undefined}>{m.tournament}</span>
        <small className="muted">{typeLabel(m.type, t)}</small>
      </td>
      <td className="m-opp">
        <span className="opp">
          {/* today's window: the category on the official list and today; a past season: then */}
          {past ? (
            <CategoryBadge cls={o.class} size="sm" />
          ) : o.class_official && o.class_today ? (
            <ClassChange from={o.class_official} to={o.class_today} />
          ) : (
            <CategoryBadge cls={o.class_today ?? o.class_official} size="sm" />
          )}
          {o.id != null ? (
            <a href={playerHref(o.id)} title={t("openPlayer")}>
              {o.name}
            </a>
          ) : (
            <span>{o.name}</span>
          )}
          {o.rank != null && <small className="muted" title={t("rankThen")}>#{o.rank.toLocaleString(locale)}</small>}
        </span>
      </td>
      {past && (
        <td className="m-own" data-label={t("youThen")}>
          <Standing cls={m.own.class} rank={m.own.rank} />
        </td>
      )}
      {!past && <td className="m-oval num">{fmt3(o.value)}</td>}
      <td className="m-score mono nowrap">{m.score || "–"}</td>
      <td className="m-res">
        <ResultPill result={m.result} how={m.how} />
        {m.chance != null && (
          <small className="m-chance muted" title={t("chanceBefore", { p: fmtChance(m.chance) })}>
            {fmtChance(m.chance)}
          </small>
        )}
      </td>
      {!past && (
        <>
          <td className="m-dw num" data-label="W">{m.counted ? <Delta value={m.delta_W} /> : "–"}</td>
          <td className="m-dc num strong" data-label="C">{m.counted ? <Delta value={m.delta_C} /> : "–"}</td>
          <td className="m-tags tags">
            {m.counted ? (
              <span className="tag tag-ok">{t("counted")}</span>
            ) : (
              <span className={`tag tag-${m.reason ?? "no_value"}`}>{t(`reason_${m.reason ?? "no_value"}` as MsgKey)}</span>
            )}
            {m.drops_after_next_official && (
              <span className="tag tag-drop" title={m.last_list ? t("dropsOut", { date: fmtDate(m.last_list, locale) }) : undefined}>
                ⏏ {t("dropsOut", { date: fmtDate(m.last_list, locale) })}
              </span>
            )}
          </td>
        </>
      )}
    </tr>
  );
}
