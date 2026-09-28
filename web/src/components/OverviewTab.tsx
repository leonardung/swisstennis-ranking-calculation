import { useMemo, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useChartColors } from "../chartTheme";
import { useI18n } from "../i18n";
import { fmt3, fmtDate, fmtDateShort, fmtInt, fmtIntDelta, signClass } from "../format";
import type { PlayerDetail } from "../types";
import { Card, CategoryBadge, ClassChange, Delta } from "./common";

interface Point {
  date: string;
  ts: number;
  W: number | null;
  C: number | null;
  rank: number | null;
  class: string | null;
  today?: boolean;
}

export default function OverviewTab({ player }: { player: PlayerDetail }) {
  const { t, locale } = useI18n();
  const o = player.official;
  const today = player.today;
  // a foreigner's fixed value: the column shows the values the matches would give instead
  const d = today?.free ? { ...today, ...today.free } : today;
  const colors = useChartColors();

  const rankDelta = o?.rank != null && d?.rank != null ? o.rank - d.rank : null; // positive = improved
  const cDelta = o?.C != null && d?.C != null ? d.C - o.C : null;
  const wDelta = o?.W != null && d?.W != null ? d.W - o.W : null;

  const points: Point[] = useMemo(() => {
    const pts: Point[] = player.history
      // the rank axis is logarithmic: a 0 cannot be drawn
      .map((h) => ({ ...h, rank: positive(h.rank), ts: Date.parse(h.date) }))
      .filter((h) => !Number.isNaN(h.ts))
      .sort((a, b) => a.ts - b.ts);
    // the values of the next list (a foreigner's fixed value, not the free one)
    const n = today;
    if (n && !pts.some((p) => p.date === n.date)) {
      pts.push({ date: n.date, ts: Date.parse(n.date), W: n.W, C: n.C, rank: positive(n.rank), class: n.class, today: true });
    }
    return pts;
  }, [player.history, today]);

  const [showRank, setShowRank] = useState(true);

  return (
    <div className="overview">
      <Card className="compare-card">
        <div className="table-wrap">
          <table className="compare">
            <thead>
              <tr>
                <th />
                <th>
                  {t("officialList")}
                  <small>{o ? fmtDate(o.date, locale) : "–"}</small>
                </th>
                <th>
                  {t("calculatedToday")}
                  <small>
                    {d ? fmtDate(d.date, locale) : "–"}
                    {today?.free && <span className="tag tag-drop free-flag">🔒 {t("freeFlag")}</span>}
                  </small>
                </th>
                <th>{t("change")}</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <th>{t("category")}</th>
                <td>
                  <CategoryBadge cls={o?.class} />
                </td>
                <td>
                  <CategoryBadge cls={d?.class} />
                </td>
                <td>{o?.class && d?.class ? <ClassChange from={o.class} to={d.class} /> : "–"}</td>
              </tr>
              <tr>
                <th>{t("rank")}</th>
                <td className="num">{fmtInt(o?.rank, locale)}</td>
                <td className="num">{fmtInt(d?.rank, locale)}</td>
                <td className="num">
                  <span className={`delta ${signClass(rankDelta)}`}>{fmtIntDelta(rankDelta)}</span>
                </td>
              </tr>
              <tr>
                <th>{t("compValue")}</th>
                <td className="num">{fmt3(o?.W)}</td>
                <td className="num">{fmt3(d?.W)}</td>
                <td className="num">
                  <Delta value={wDelta} />
                </td>
              </tr>
              <tr>
                <th>{t("riskBonus")}</th>
                <td className="num">{o?.C != null && o?.W != null ? fmt3(o.C - o.W) : "–"}</td>
                <td className="num">{fmt3(d?.R)}</td>
                <td />
              </tr>
              <tr className="strong-row">
                <th>{t("rankingValue")}</th>
                <td className="num">{fmt3(o?.C)}</td>
                <td className="num">{fmt3(d?.C)}</td>
                <td className="num">
                  <Delta value={cDelta} />
                </td>
              </tr>
              <tr>
                <th>{t("startValue")}</th>
                <td />
                <td className="num">{fmt3(d?.w0)}</td>
                <td />
              </tr>
              <tr>
                <th>{t("matchesCounted")}</th>
                <td />
                <td className="num">{fmtInt(d?.n_matches, locale)}</td>
                <td />
              </tr>
            </tbody>
          </table>
        </div>
        {!o && <p className="hint">{t("noOfficial")}</p>}
        {o && o.current === false && <p className="hint">{t("officialOld", { date: fmtDate(o.date, locale) })}</p>}
        {!d && <p className="hint">{t("noToday")}</p>}
        {today?.free && (
          <p className="hint hint-info">
            🔒{" "}
            {t("noteFree")}
          </p>
        )}
        {d?.note === "assigned" && !today?.free && <p className="hint hint-info">ⓘ {t("noteAssigned")}</p>}
        {d?.note === "classified" && <p className="hint hint-info">ⓘ {t("noteClassified")}</p>}
      </Card>

      <Card
        title={t("history")}
        actions={
          <div className="seg" role="group">
            <button className={showRank ? "active" : ""} onClick={() => setShowRank(true)}>
              {t("chartRank")}
            </button>
            <button className={!showRank ? "active" : ""} onClick={() => setShowRank(false)}>
              {t("chartValues")}
            </button>
          </div>
        }
      >
        {points.length === 0 ? (
          <p className="muted">{t("historyEmpty")}</p>
        ) : (
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
                {showRank ? (
                  <YAxis
                    reversed
                    scale="log"
                    stroke={colors.axis}
                    tick={{ fontSize: 12 }}
                    tickLine={false}
                    axisLine={false}
                    width={56}
                    domain={["dataMin", "dataMax"]}
                    allowDecimals={false}
                  />
                ) : (
                  <YAxis
                    stroke={colors.axis}
                    tick={{ fontSize: 12 }}
                    tickLine={false}
                    axisLine={false}
                    width={44}
                    domain={["auto", "auto"]}
                    tickFormatter={(v: number) => v.toFixed(1)}
                  />
                )}
                <Tooltip content={<HistoryTooltip showRank={showRank} />} />
                {showRank ? (
                  <Line type="monotone" dataKey="rank" name={t("rank")} stroke={colors.s1} strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} connectNulls isAnimationActive={false} />
                ) : (
                  <>
                    <Legend verticalAlign="top" height={28} iconType="plainline" itemSorter={null} />
                    <Line type="monotone" dataKey="W" name="W" stroke={colors.s1} strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} connectNulls isAnimationActive={false} />
                    <Line type="monotone" dataKey="C" name="C" stroke={colors.s2} strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} connectNulls isAnimationActive={false} />
                  </>
                )}
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
        {points.length > 0 && <p className="hint small">{t(showRank ? "historyHelpRank" : "historyHelpValues")}</p>}
      </Card>
    </div>
  );
}

const positive = (v: number | null | undefined) => (v != null && v > 0 ? v : null);

function HistoryTooltip({ active, payload, showRank }: { active?: boolean; payload?: { payload: Point }[]; showRank: boolean }) {
  const { t, locale } = useI18n();
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="chart-tip">
      <div className="chart-tip-head">
        {fmtDate(p.date, locale)} {p.today && <em>({t("today")})</em>} <CategoryBadge cls={p.class} size="sm" />
      </div>
      {showRank ? (
        <div>
          {t("rank")}: <strong>{fmtInt(p.rank, locale)}</strong>
        </div>
      ) : (
        <>
          <div>
            W: <strong>{fmt3(p.W)}</strong>
          </div>
          <div>
            C: <strong>{fmt3(p.C)}</strong>
          </div>
        </>
      )}
    </div>
  );
}
