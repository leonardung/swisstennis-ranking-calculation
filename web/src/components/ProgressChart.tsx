import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useChartColors } from "../chartTheme";
import { useI18n } from "../i18n";
import { fmt3, fmtDate, fmtDateShort, fmtInt } from "../format";
import type { Match, MatchesResponse, ProgressPoint } from "../types";
import { CategoryBadge, Delta, ResultPill } from "./common";

type Progress = NonNullable<MatchesResponse["progress"]>;
type Point = ProgressPoint & { C: number; ts: number };

/** The player's C through today's window, with the category thresholds around it. */
export default function ProgressChart({ progress, matches }: { progress: Progress; matches: Match[] }) {
  const { t, locale } = useI18n();
  const colors = useChartColors();
  const points = progress.points
    .filter((p): p is ProgressPoint & { C: number } => p.C != null)
    .map((p) => ({ ...p, ts: Date.parse(p.date) }));
  if (points.length < 2) return null;
  const byId = new Map(matches.map((m) => [m.id, m]));
  // a tick on the first of each month (the axis would otherwise put one on each match day)
  const first = points[0].ts, last = points[points.length - 1].ts;
  const ticks: number[] = [];
  for (let d = new Date(first); d.getTime() <= last; d.setUTCMonth(d.getUTCMonth() + 1, 1)) {
    const tick = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1);
    if (tick >= first) ticks.push(tick);
  }

  // the values, widened to the category bound just above and just below them
  const cs = points.map((p) => p.C);
  const lo = Math.min(...cs), hi = Math.max(...cs);
  const bounds = progress.thresholds.filter((b): b is { class: string; C: number } => b.C != null);
  const above = Math.min(...bounds.filter((b) => b.C > hi).map((b) => b.C));
  const below = Math.max(...bounds.filter((b) => b.C <= lo).map((b) => b.C));
  const top = Number.isFinite(above) ? above : hi;
  const bottom = Number.isFinite(below) ? below : lo;
  // whole numbers: even ticks
  const domain = [Math.floor(bottom - 0.1), Math.ceil(top + 0.1)];

  return (
    <div className="chart-box progress-chart">
      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={points} margin={{ top: 10, right: 36, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={colors.grid} vertical={false} />
          <XAxis
            dataKey="ts"
            type="number"
            scale="time"
            domain={["dataMin", "dataMax"]}
            ticks={ticks}
            tickFormatter={(v: number) => fmtDateShort(new Date(v).toISOString().slice(0, 10), locale)}
            stroke={colors.axis}
            tick={{ fontSize: 12 }}
            tickLine={false}
            minTickGap={24}
          />
          <YAxis
            stroke={colors.axis}
            tick={{ fontSize: 12 }}
            tickLine={false}
            axisLine={false}
            width={44}
            domain={domain}
            allowDecimals={false}
            tickFormatter={(v: number) => v.toFixed(1)}
          />
          {bounds
            .filter((b) => b.C >= bottom && b.C <= top)
            .map((b) => (
              <ReferenceLine
                key={b.class}
                y={b.C}
                stroke={colors.axis}
                strokeDasharray="4 4"
                strokeOpacity={0.7}
                label={{ value: b.class, position: "right", fill: colors.axis, fontSize: 12 }}
              />
            ))}
          <Tooltip content={<ProgressTooltip matches={byId} />} />
          <Line
            type="stepAfter"
            dataKey="C"
            name="C"
            stroke={colors.s2}
            strokeWidth={2}
            dot={{ r: 3 }}
            activeDot={{ r: 5 }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
      <p className="hint small">{t("progressHelp")}</p>
    </div>
  );

}

function ProgressTooltip({ active, payload, matches }: { active?: boolean; payload?: { payload: Point }[]; matches: Map<number, Match> }) {
  const { t, locale } = useI18n();
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="chart-tip">
      <div className="chart-tip-head">{fmtDate(p.date, locale)}</div>
      <div>
        C: <strong>{fmt3(p.C)}</strong>
        {p.rank != null && (
          <>
            {" · "}
            {t("rank")}: <strong>{fmtInt(p.rank, locale)}</strong>
          </>
        )}
      </div>
      {p.matches.map(({ id, delta_C }) => {
        const m = matches.get(id);
        if (!m) return null;
        return (
          <div key={id} className="chart-tip-row progress-tip-match">
            <ResultPill result={m.result} how={m.how} />
            <CategoryBadge cls={m.opponent.class} size="sm" />
            <span className="progress-tip-name">{m.opponent.name}</span>
            <span className="muted">{m.score}</span>
            {delta_C == null ? <span className="muted progress-tip-gain">{t("reason_discarded_loss")}</span> : <Delta value={delta_C} />}
          </div>
        );
      })}
    </div>
  );
}
