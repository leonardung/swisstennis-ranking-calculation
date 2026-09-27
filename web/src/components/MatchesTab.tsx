import { useMemo, useState } from "react";
import { api } from "../api";
import { playerHref, useAsync } from "../hooks";
import { useI18n, type MsgKey } from "../i18n";
import { fmt3, fmtDate } from "../format";
import type { Match } from "../types";
import { AsyncStatus, Card, CategoryBadge, ClassChange, Delta, Hint, ResultPill } from "./common";

type Filter = "all" | "wins" | "losses" | "not_counted" | "dropping";
type SortKey = "date" | "delta";

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

export default function MatchesTab({ playerId }: { playerId: number }) {
  const { t, locale } = useI18n();
  const st = useAsync((s) => api.matches(playerId, s), [playerId]);
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: "date", desc: true });

  const all = st.data?.matches ?? [];

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
  }, [all, filter, sort]);

  if (!st.data) return <AsyncStatus {...st} />;

  const toggleSort = (key: SortKey) =>
    setSort((s) => (s.key === key ? { key, desc: !s.desc } : { key, desc: true }));
  const sortMark = (key: SortKey) => (sort.key === key ? (sort.desc ? " ▼" : " ▲") : "");

  return (
    <Card
      title={t("matchesTitle")}
      actions={
        <span className="muted small">
          {t("windowFromTo", { start: fmtDate(st.data.window.start, locale), end: fmtDate(st.data.window.end, locale) })}
        </span>
      }
    >
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

      <div className="toolbar">
        <div className="seg" role="group">
          {FILTERS.map(([k, label]) => (
            <button key={k} className={filter === k ? "active" : ""} onClick={() => setFilter(k)}>
              {t(label)} <span className="count">{counts[k]}</span>
            </button>
          ))}
        </div>
        <div className="seg" role="group" aria-label={t("sortBy")}>
          <button className={sort.key === "date" ? "active" : ""} onClick={() => toggleSort("date")}>
            {t("sortDate")}
            {sortMark("date")}
          </button>
          <button className={sort.key === "delta" ? "active" : ""} onClick={() => toggleSort("delta")}>
            {t("sortDelta")}
            {sortMark("delta")}
          </button>
        </div>
      </div>

      <div className="table-wrap">
        <table className="data matches">
          <thead>
            <tr>
              <th className="sortable" onClick={() => toggleSort("date")}>
                {t("date")}
                {sortMark("date")}
              </th>
              <th>{t("tournament")}</th>
              <th>{t("opponent")}</th>
              <th className="num">
                <Hint label={t("oppValue")} text={t("oppValueHelp")} />
              </th>
              <th>{t("score")}</th>
              <th>{t("result")}</th>
              <th className="num">
                <Hint label="Δ W" text={t("deltaWHelp")} align="end" />
              </th>
              <th className="num sortable" onClick={() => toggleSort("delta")}>
                <Hint label="Δ C" text={t("deltaCHelp")} align="end" />
                {sortMark("delta")}
              </th>
              <th>{t("status")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={9} className="empty">
                  {t("noMatches")}
                </td>
              </tr>
            )}
            {rows.map((m) => (
              <MatchRow key={m.id} m={m} />
            ))}
          </tbody>
        </table>
      </div>
      <p className="hint small">{t("deltaHelp")}</p>
    </Card>
  );
}

function MatchRow({ m }: { m: Match }) {
  const { t, locale } = useI18n();
  return (
    <tr className={`${m.counted ? "" : "not-counted"} ${m.result === "win" ? "row-win" : "row-loss"}`}>
      <td className="nowrap">{fmtDate(m.date, locale)}</td>
      <td className="tourn">
        <span>{m.tournament}</span>
        <small className="muted">{typeLabel(m.type, t)}</small>
      </td>
      <td className="nowrap">
        <span className="opp">
          {m.opponent.class_official && m.opponent.class ? (
            <ClassChange from={m.opponent.class_official} to={m.opponent.class} />
          ) : (
            <CategoryBadge cls={m.opponent.class ?? m.opponent.class_official} size="sm" />
          )}
          {m.opponent.id != null ? (
            <a href={playerHref(m.opponent.id)} title={t("openPlayer")}>
              {m.opponent.name}
            </a>
          ) : (
            <span>{m.opponent.name}</span>
          )}
        </span>
      </td>
      <td className="num">{fmt3(m.opponent.value)}</td>
      <td className="mono nowrap">{m.score || "–"}</td>
      <td>
        <ResultPill result={m.result} how={m.how} />
      </td>
      <td className="num">{m.counted ? <Delta value={m.delta_W} /> : "–"}</td>
      <td className="num strong">{m.counted ? <Delta value={m.delta_C} /> : "–"}</td>
      <td className="tags">
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
    </tr>
  );
}
