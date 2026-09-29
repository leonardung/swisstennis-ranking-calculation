import { useMemo } from "react";
import { api } from "../api";
import { playerHref, useAsync, useDebounced, useUrlState } from "../hooks";
import { useI18n, type MsgKey } from "../i18n";
import { CATEGORIES, fmt3, fmtDate, fmtInt } from "../format";
import type { Match, PlayerDetail } from "../types";
import { AsyncStatus, Card, CategoryBadge, Delta, Hint, ResultPill, Spinner } from "./common";
import SearchBox from "./SearchBox";
import { SimRow } from "./SimulatorTab";

/** The player's own what-if (made-up results, played matches left out, matches against players)
 * and what they need to move up. */
export default function SimulationTab({ player }: { player: PlayerDetail }) {
  return (
    <div className="simulator">
      <PredictCard player={player} />
      <MoveUpCard playerId={player.id} />
    </div>
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

// the backend's limits (see predict in web.py): made-up results are outcome:count:category:percentile
const MAX_PER_ROW = 30;
const MAX_RESULTS = 60;
const MAX_VS = 30;

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

// no row at all is "-" (an empty parameter shows the starting row)
const formatHypos = (h: Hypo[]) => h.map((x) => `${x.win ? "win" : "loss"}:${x.count}:${x.cls}:${x.percentile}`).join(",") || "-";

interface Vs {
  win: boolean;
  id: number;
}

function parseVs(v: string): Vs[] {
  return v
    .split(",")
    .map((x) => x.split(":"))
    .filter((p) => p.length === 2 && Number(p[1]) > 0)
    .map(([o, id]) => ({ win: o !== "loss", id: Number(id) }));
}

const formatVs = (v: Vs[]) => v.map((x) => `${x.win ? "win" : "loss"}:${x.id}`).join(",");

/** A match of today's window that enters the calculation (and can be left out). */
const inCalculation = (m: Match) => m.reason == null || m.reason === "discarded_loss";

/** The player's values after a what-if the visitor makes up (kept in the URL): all the changes in
 * one recalculation. */
function PredictCard({ player }: { player: PlayerDetail }) {
  const { t } = useI18n();
  const own = player.today?.class && CATEGORIES.includes(player.today.class) ? player.today.class : "R5";
  const [param, setParam] = useUrlState("pred", "");
  const [rmParam, setRm] = useUrlState("rm", "");
  const [vsParam, setVs] = useUrlState("vs", "");
  const rows = param ? parseHypos(param) : [{ win: true, count: 1, cls: own, percentile: 50 }];
  const set = (h: Hypo[]) => setParam(formatHypos(h));
  const update = (i: number, change: Partial<Hypo>) => set(rows.map((r, j) => (j === i ? { ...r, ...change } : r)));
  const total = rows.reduce((n, r) => n + r.count, 0);
  const vs = parseVs(vsParam);
  const setVsList = (v: Vs[]) => setVs(formatVs(v));
  const removed = useMemo(() => new Set(rmParam.split(",").map(Number).filter((x) => x > 0)), [rmParam]);
  const played = useAsync((s) => api.matches(player.id, 0, s), [player.id]);
  const list = played.data?.matches.filter(inCalculation) ?? [];
  const toggle = (id: number) => {
    const next = new Set(removed);
    if (!next.delete(id)) next.add(id);
    setRm([...next].join(","));
  };

  const tooMany = total + vs.length > MAX_RESULTS || vs.length > MAX_VS;
  const q = {
    results: rows.length ? formatHypos(rows) : "",
    remove: [...removed].join(","),
    add: formatVs(vs),
  };
  const empty = !q.results && !q.remove && !q.add;
  const query = useDebounced(tooMany || empty ? "" : new URLSearchParams(q).toString(), 400);
  const st = useAsync(query ? (s) => api.predict(player.id, Object.fromEntries(new URLSearchParams(query)) as typeof q, s) : null, [player.id, query]);
  const d = st.data;

  const pickVs = (p: { id: number; gender?: string | null }) => {
    if (p.id === player.id || (p.gender && p.gender !== player.gender) || vs.length >= MAX_VS) return;
    setVsList([...vs, { win: true, id: p.id }]);
  };

  return (
    <Card title={t("predictTitle")}>
      <p className="muted">{t("predictIntro")}</p>

      <h3 className="predict-sub">{t("predictHypoTitle")}</h3>
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

      <h3 className="predict-sub">{t("predictVsTitle")}</h3>
      <p className="muted small">{t("predictVsIntro")}</p>
      <div className="predict-rows">
        {vs.map((v, i) => (
          <VsRow
            key={`${i}-${v.id}`}
            vs={v}
            onChange={(win) => setVsList(vs.map((x, j) => (j === i ? { ...x, win } : x)))}
            onRemove={() => setVsList(vs.filter((_, j) => j !== i))}
          />
        ))}
        {vs.length < MAX_VS && <SearchBox className="sim-search predict-vs-search" placeholder={t("predictVsSearch")} gender={player.gender ?? undefined} onSelect={pickVs} />}
      </div>

      <details className="predict-played" open={removed.size > 0 || undefined}>
        <summary>
          <h3 className="predict-sub">{t("predictPlayedTitle")}</h3>
          {removed.size > 0 && <span className="tag">{t("predictPlayedOut", { n: removed.size })}</span>}
        </summary>
        <p className="muted small">{t("predictPlayedIntro")}</p>
        {!played.data ? (
          <AsyncStatus {...played} />
        ) : list.length === 0 ? (
          <p className="muted">{t("predictPlayedNone")}</p>
        ) : (
          <>
            {removed.size > 0 && (
              <button className="btn btn-ghost predict-add" onClick={() => setRm("")}>
                {t("predictPlayedAll")}
              </button>
            )}
            <ul className="played-list">
              {list.map((m) => (
                <PlayedRow key={m.id} m={m} kept={!removed.has(m.id)} onToggle={() => toggle(m.id)} />
              ))}
            </ul>
          </>
        )}
      </details>

      {total > MAX_RESULTS && <p className="hint neg">{t("predictTooMany", { n: MAX_RESULTS })}</p>}
      {vs.length >= MAX_VS && <p className="hint neg">{t("predictVsTooMany", { n: MAX_VS })}</p>}
      <div className="predict-result">
        {empty ? (
          <p className="muted">{t("predictNothing")}</p>
        ) : tooMany ? null : d ? (
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
                <SimRow label={t("predictAfter")} good={(d.after.C ?? 0) >= (d.before.C ?? 0)} before={d.before} after={d.after} />
              </tbody>
            </table>
          </div>
        ) : (
          <AsyncStatus {...st} />
        )}
        {st.loading && d && <Spinner />}
      </div>
      <p className="hint small">{t("predictHelp")}</p>
    </Card>
  );
}

/** A match against a given player: win or loss, the player's name and category today. */
function VsRow({ vs, onChange, onRemove }: { vs: Vs; onChange: (win: boolean) => void; onRemove: () => void }) {
  const { t } = useI18n();
  const st = useAsync((s) => api.player(vs.id, s), [vs.id]);
  const p = st.data?.id === vs.id ? st.data : null;
  return (
    <div className="predict-row">
      <select value={vs.win ? "win" : "loss"} onChange={(e) => onChange(e.target.value === "win")} aria-label={t("result")}>
        <option value="win">{t("win")}</option>
        <option value="loss">{t("loss")}</option>
      </select>
      <span className="predict-vs-name">
        {p ? (
          <>
            <CategoryBadge cls={p.today?.class} size="sm" />
            <a href={playerHref(p.id)}>{p.name}</a>
          </>
        ) : st.error ? (
          <span className="neg">#{vs.id}</span>
        ) : (
          <Spinner />
        )}
      </span>
      <button className="btn btn-ghost predict-remove" onClick={onRemove} aria-label={t("predictRemove")} title={t("predictRemove")}>
        ✕
      </button>
    </div>
  );
}

/** A played match of today's window with a checkbox: unticked, it is left out. */
function PlayedRow({ m, kept, onToggle }: { m: Match; kept: boolean; onToggle: () => void }) {
  const { locale } = useI18n();
  const o = m.opponent;
  return (
    <li className={kept ? "" : "left-out"}>
      <label>
        <input type="checkbox" checked={kept} onChange={onToggle} />
        <span className="played-date nowrap" title={fmtDate(m.date, locale)}>
          {new Date(`${m.date}T12:00:00`).toLocaleDateString(locale, { day: "numeric", month: "short" })}
        </span>
        <ResultPill result={m.result} how={m.how} />
        <span className="played-opp">
          <CategoryBadge cls={o.class_today ?? o.class_official} size="sm" />
          <span className="played-name">{o.name}</span>
        </span>
        <span className="played-score mono nowrap">{m.score}</span>
        <span className="played-dc num">{m.counted ? <Delta value={m.delta_C} /> : "–"}</span>
      </label>
    </li>
  );
}
