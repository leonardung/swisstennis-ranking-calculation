import { useState } from "react";
import { api } from "../api";
import { playerHref, useAsync } from "../hooks";
import { useI18n, type MsgKey } from "../i18n";
import { fmt3, fmtDate } from "../format";
import type { BoardKey, BoardRow, Highlights as Data, HlGroup, PersonRef } from "../types";
import { AsyncStatus, Card, CategoryBadge, ClassChange, Delta, Hint } from "./common";
import MatchListDialog, { opens, type MatchQuery } from "./MatchListDialog";

const SHOWN = 5;
const GENDERS: [string, MsgKey][] = [
  ["M", "men"],
  ["F", "women"],
];
const GROUPS: HlGroup[] = ["N1-R3", "R4-R9"];
// board, title, explanation shown on the title (ratios: the minimum played, filled in)
const BOARDS: [BoardKey, MsgKey, MsgKey][] = [
  ["tiebreaks", "hlTiebreaks", "hlMinTiebreaks"],
  ["super_tiebreaks", "hlSuperTiebreaks", "hlMinSuperTiebreaks"],
  ["three_sets", "hlThreeSets", "hlMinThreeSets"],
  ["comebacks", "hlComebacks", "hlMinComebacks"],
  ["bagels", "hlBagels", "hlMinBagels"],
  ["streak", "hlStreak", "hlStreakHelp"],
];

interface Filter {
  gender: string;
  group: HlGroup;
}

function Seg<T extends string>({ value, options, onChange }: { value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <div className="seg" role="group">
      {options.map(([k, label]) => (
        <button key={k} className={value === k ? "active" : ""} onClick={() => onChange(k)}>
          {label}
        </button>
      ))}
    </div>
  );
}

function GenderSeg({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { t } = useI18n();
  return <Seg value={value} options={GENDERS.map(([g, k]) => [g, t(k)])} onChange={onChange} />;
}

/** "Show more / Show less" under a list longer than SHOWN. */
function More({ total, open, toggle }: { total: number; open: boolean; toggle: () => void }) {
  const { t } = useI18n();
  if (total <= SHOWN) return null;
  return (
    <button className="btn btn-ghost hl-more" onClick={toggle}>
      {open ? t("hlShowLess") : t("hlShowAll")}
    </button>
  );
}

function PlayerLink({ p }: { p: PersonRef }) {
  // inside a row that opens a match list: the link only goes to the player
  return (
    <a href={playerHref(p.id)} onClick={(e) => e.stopPropagation()}>
      {p.name}
    </a>
  );
}

function Upsets({ data, filter, open, toggle }: { data: Data["upsets"]; filter: Filter; open: boolean; toggle: () => void }) {
  const { t, locale } = useI18n();
  const rows = data.matches.filter((u) => u.gender === filter.gender && u.group === filter.group);
  return (
    <Card title={t("hlUpsetsTitle")} className="hl-card hl-pair">
      <p className="hint small">{t("hlUpsetsIntro", { from: fmtDate(data.from, locale), to: fmtDate(data.to, locale) })}</p>
      {rows.length === 0 ? (
        <p className="muted">{t("hlUpsetsNone")}</p>
      ) : (
        <ol className="hl-list">
          {rows.slice(0, open ? undefined : SHOWN).map((u, i) => (
            <li key={i} className="hl-upset">
              <div className="hl-upset-main">
                <span className="hl-who">
                  <CategoryBadge cls={u.winner.class} size="sm" />
                  <PlayerLink p={u.winner} />
                </span>
                <span className="muted small">{t("hlBeat")}</span>
                <span className="hl-who">
                  <CategoryBadge cls={u.loser.class} size="sm" />
                  <PlayerLink p={u.loser} />
                </span>
              </div>
              <div className="hl-upset-side">
                <Hint label={<span className="tag tag-ok">{t("hlLevels", { n: u.levels })}</span>} text={t("hlLevelsHelp")} align="end" />
                <span className="mono small">{u.score}</span>
              </div>
              <small className="muted hl-upset-meta">
                {fmtDate(u.date, locale)}
                {u.tournament && ` · ${u.tournament}`}
              </small>
            </li>
          ))}
        </ol>
      )}
      <More total={rows.length} open={open} toggle={toggle} />
    </Card>
  );
}

function Movers({ data, filter, open, toggle }: { data: Data["movers"]; filter: Filter; open: boolean; toggle: () => void }) {
  const { t, locale } = useI18n();
  const [period, setPeriod] = useState<"today" | "season">("today");
  const list = (period === "season" && data.season) || data.today;
  const rows = list.players.filter((m) => m.gender === filter.gender && m.group === filter.group);
  const periods: ["today" | "season", string][] = [["today", t("hlMoversToday")]];
  if (data.season) periods.push(["season", t("hlMoversSeason")]);
  const intro = period === "season" ? "hlMoversSeasonIntro" : "hlMoversTodayIntro";
  return (
    <Card title={t("hlMoversTitle")} className="hl-card hl-pair" actions={<Seg value={period} options={periods} onChange={setPeriod} />}>
      <p className="hint small">{t(intro, { from: fmtDate(list.from, locale), to: fmtDate(list.to, locale) })}</p>
      {rows.length === 0 ? (
        <p className="muted">{t("hlNone")}</p>
      ) : (
        <ol className="hl-list">
          {rows.slice(0, open ? undefined : SHOWN).map((m, i) => (
            <li key={m.player.id} className="hl-mover">
              <span className="hl-pos">{i + 1}</span>
              <PlayerLink p={m.player} />
              <ClassChange from={m.from.class} to={m.to.class} />
              <span className="hl-mover-val">
                <Delta value={m.gain} />
                <small className="muted">
                  {fmt3(m.from.C)} → {fmt3(m.to.C)}
                </small>
              </span>
            </li>
          ))}
        </ol>
      )}
      <More total={rows.length} open={open} toggle={toggle} />
    </Card>
  );
}

function BoardDetail({ k, r }: { k: BoardKey; r: BoardRow }) {
  const { t, locale } = useI18n();
  if (k !== "streak") return <small className="muted">{t("hlWonOf", { won: r.won ?? 0, n: (r.won ?? 0) + (r.lost ?? 0) })}</small>;
  return (
    <small className="muted">
      {fmtDate(r.start, locale)} → {r.ongoing ? <span className="pos">{t("hlOngoing")}</span> : fmtDate(r.end, locale)}
    </small>
  );
}

function Leaderboards({ data }: { data: Data["leaderboards"] }) {
  const { t, locale } = useI18n();
  const [gender, setGender] = useState("M");
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState<MatchQuery | null>(null);
  const boards = data.players[gender];
  const pct = (v: number) => new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 0 }).format(v);
  const longest = Math.max(0, ...BOARDS.map(([k]) => boards?.[k]?.length ?? 0));
  return (
    <Card title={t("hlBoardsTitle")} className="hl-card" actions={<GenderSeg value={gender} onChange={setGender} />}>
      {query && <MatchListDialog key={query.key} query={query} onClose={() => setQuery(null)} />}
      <p className="hint small">{t("hlBoardsIntro", { since: fmtDate(data.since, locale) })}</p>
      <div className="hl-boards">
        {BOARDS.map(([k, label, help]) => (
          <div key={k} className="hl-board">
            <h4>
              <Hint label={t(label)} text={t(help, { n: data.min_played[k] ?? 0 })} />
            </h4>
            <ol className="hl-list">
              {(boards?.[k] ?? []).slice(0, open ? undefined : SHOWN).map((r, i) => (
                <li
                  key={r.player.id}
                  className="hl-board-row opens"
                  {...opens(
                    () =>
                      setQuery({
                        key: `${k}:${r.player.id}`,
                        title: `${r.player.name} · ${t(label)}`,
                        load: (sig) => api.boardMatches(k, r.player.id, sig),
                      }),
                    t("showMatches"),
                  )}
                >
                  <span className="hl-pos">{i + 1}</span>
                  <span className="hl-board-name">
                    <span className="hl-who">
                      <CategoryBadge cls={r.player.class} size="sm" />
                      <PlayerLink p={r.player} />
                    </span>
                    <BoardDetail k={k} r={r} />
                  </span>
                  <strong className="hl-board-val">{k === "streak" ? r.value : pct(r.value)}</strong>
                </li>
              ))}
            </ol>
          </div>
        ))}
      </div>
      <More total={longest} open={open} toggle={() => setOpen(!open)} />
    </Card>
  );
}

export default function Highlights() {
  const { t } = useI18n();
  const st = useAsync((s) => api.highlights(s), []);
  const [filter, setFilter] = useState<Filter>({ gender: "M", group: "N1-R3" });
  // "show more" opens the upsets and the movers together (side by side)
  const [open, setOpen] = useState(false);
  const toggle = () => setOpen(!open);
  if (!st.data) return <AsyncStatus {...st} />;
  return (
    <>
      <div className="hl-filter">
        <GenderSeg value={filter.gender} onChange={(gender) => setFilter({ ...filter, gender })} />
        <div title={t("hlFilterGroupHelp")}>
          <Seg
            value={filter.group}
            options={GROUPS.map((g) => [g, g.replace("-", "–")])}
            onChange={(group) => setFilter({ ...filter, group })}
          />
        </div>
      </div>
      <div className="highlights">
        <Upsets data={st.data.upsets} filter={filter} open={open} toggle={toggle} />
        <Movers data={st.data.movers} filter={filter} open={open} toggle={toggle} />
        <Leaderboards data={st.data.leaderboards} />
      </div>
    </>
  );
}
