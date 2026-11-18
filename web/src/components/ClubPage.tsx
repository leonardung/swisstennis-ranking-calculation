import { useEffect, useState } from "react";
import { api } from "../api";
import { trackView } from "../analytics";
import { CLUB_TABS, clubHref, playerHref, tournamentHref, useAsync, useUrlState, type ClubTab } from "../hooks";
import { useI18n, type MsgKey, type TFunc } from "../i18n";
import { categoryIndex, fmt3 } from "../format";
import { exportTable, type ExportValue } from "../export";
import type { ClubDetail, ClubPlayer } from "../types";
import { AsyncStatus, Card, ClassChange, RankChange, Seg, sortRows, useSort, type Sort } from "./common";
import ExportDialog from "./ExportDialog";
import { dateRange } from "./tournamentUtil";

/** "45+": 45 and older; "16&U": 16 and younger; else null (actives). */
function ageLimit(age: string | null): { senior: boolean; n: number } | null {
  const m = /^(\d+)(\+|&U)$/.exec(age ?? "");
  return m ? { senior: m[2] === "+", n: Number(m[1]) } : null;
}

/** The age categories of the club's players: juniors youngest first, then seniors. */
function ageOptions(players: ClubPlayer[]): string[] {
  const ages = [...new Set(players.map((p) => p.age).filter((a) => ageLimit(a) != null))] as string[];
  const key = (a: string) => {
    const l = ageLimit(a)!;
    return l.senior ? 100 + l.n : l.n;
  };
  return ages.sort((a, b) => key(a) - key(b));
}

/** Juniors youngest first, then actives, then seniors. */
function ageOrder(age: string | null): number | null {
  const l = ageLimit(age);
  return l ? (l.senior ? 100 + l.n : l.n) : age ? 50 : null;
}

/** class and rank: today's (else the official one); classOff and rankOff: the official one */
type Key = "c" | "name" | "age" | "class" | "classOff" | "rank" | "rankOff";

const fold = (s: string) => s.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
const classOrder = (cls: string | null | undefined) => {
  const i = categoryIndex(cls);
  return i < 0 ? null : i;
};

const VALUE: Record<Key, (p: ClubPlayer) => number | string | null | undefined> = {
  c: (p) => p.today?.C ?? p.official?.C,
  name: (p) => fold(p.name),
  age: (p) => ageOrder(p.age),
  class: (p) => classOrder(p.today?.class ?? p.official?.class),
  classOff: (p) => classOrder(p.official?.class),
  rank: (p) => p.today?.rank ?? p.official?.rank,
  rankOff: (p) => p.official?.rank,
};

/** The "Sort by" menu: each sort the strongest (or A to Z, youngest) first. */
const SORTS: [Key, MsgKey, boolean][] = [
  ["c", "sortC", true],
  ["classOff", "sortClassOfficial", false],
  ["class", "sortClassToday", false],
  ["rankOff", "sortRankOfficial", false],
  ["rank", "sortRankToday", false],
  ["name", "sortName", false],
  ["age", "clubAge", false],
];

type Gender = "M" | "F" | "all";

const round3 = (n: number | null | undefined) => (n == null ? null : Math.round(n * 1000) / 1000);

/** The columns one can export, in their order: the value of a player at a position (from 1);
 * the C values unticked at first. */
const EXPORT: [string, (t: TFunc) => string, (p: ClubPlayer, pos: number, t: TFunc) => ExportValue, boolean?][] = [
  ["pos", () => "#", (_, pos) => pos],
  ["name", (t) => t("clubPlayer"), (p) => p.name],
  ["gender", (t) => t("colGender"), (p, _, t) => (p.gender === "M" ? t("sexM") : p.gender === "F" ? t("sexF") : null)],
  ["age", (t) => t("clubAge"), (p) => p.age],
  ["classOff", (t) => t("sortClassOfficial"), (p) => p.official?.class],
  ["class", (t) => t("sortClassToday"), (p) => p.today?.class ?? p.official?.class],
  ["rankOff", (t) => t("sortRankOfficial"), (p) => p.official?.rank],
  ["rank", (t) => t("sortRankToday"), (p) => p.today?.rank ?? p.official?.rank],
  ["cOff", (t) => t("colCOfficial"), (p) => round3(p.official?.C), false],
  ["c", (t) => t("colCToday"), (p) => round3(p.today?.C ?? p.official?.C), false],
];

/** A file name from texts: letters, digits and dashes. */
const slug = (...parts: (string | null | undefined)[]) =>
  parts
    .filter(Boolean)
    .join("-")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^A-Za-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .toLowerCase();

export default function ClubPage({ id, tab }: { id: number; tab: ClubTab }) {
  const { t } = useI18n();
  const st = useAsync((s) => api.club(id, s), [id]);
  const c = st.data;

  useEffect(() => {
    if (c) {
      document.title = `${c.name} · ${t("appName")}`;
      trackView();
    }
  }, [c, t]);

  if (!c) {
    return (
      <AsyncStatus
        {...st}
        notFound={
          <div className="state-screen small">
            <h2>{t("clubNotFound")}</h2>
            <a className="btn" href="/">
              {t("backHome")}
            </a>
          </div>
        }
      />
    );
  }

  return (
    <div className="club-page">
      <section className="player-head">
        <div className="player-id">
          <h1>{c.name}</h1>
          <div className="muted player-sub">
            {c.city && <span>{c.city}</span>}
            <span>{t("clubPlayersCount", { n: c.players.length })}</span>
          </div>
        </div>
      </section>
      <nav className="tabs" role="tablist">
        {CLUB_TABS.map((k) => (
          <a key={k} role="tab" aria-selected={tab === k} className={tab === k ? "tab active" : "tab"} href={clubHref(id, k)}>
            {k === "tournaments" ? `${t("clubTournaments")} (${c.tournaments.length})` : `${t("clubTabPlayers")} (${c.players.length})`}
          </a>
        ))}
      </nav>
      <div className="tab-body">{tab === "tournaments" ? <ClubTournaments club={c} /> : <ClubPlayers club={c} />}</div>
    </div>
  );
}

/** The club's players of one gender (or both) and one licence age category (or all), sortable by
 * any column, and exported as shown. */
function ClubPlayers({ club: c }: { club: ClubDetail }) {
  const { t, locale } = useI18n();
  const [genderParam, setGender] = useUrlState("g", "M");
  const gender: Gender = genderParam === "F" || genderParam === "all" ? genderParam : "M";
  const [age, setAge] = useUrlState("age", "");
  const [sort, th, setSort] = useSort<Key>("sort", { key: "c", desc: true });
  const [exporting, setExporting] = useState(false);
  const ages = ageOptions(c.players);
  const shown = c.players.filter((p) => (gender === "all" || p.gender === gender) && (!age || p.age === age));
  const rows = sortRows(shown, VALUE[sort.key] ?? VALUE.c, sort.desc);
  const genderLabel = gender === "all" ? t("clubAllGenders") : gender === "M" ? t("men") : t("women");
  const sex = (p: ClubPlayer) => (p.gender === "M" ? t("sexM") : p.gender === "F" ? t("sexF") : null);
  // both genders: the gender before the age
  const ageText = (p: ClubPlayer) => [gender === "all" ? sex(p) : null, p.age].filter(Boolean).join(" · ");
  const sortValue = SORTS.some(([k]) => k === sort.key) ? sort.key : "c";

  const onExport = async (format: Parameters<typeof exportTable>[0], keys: string[]) => {
    const cols = EXPORT.filter(([k]) => keys.includes(k));
    await exportTable(format, {
      fileName: slug(c.name ?? String(c.id), gender === "all" ? null : gender, age.replace("&", "")),
      title: c.name ?? String(c.id),
      subtitle: [genderLabel, age || t("clubAllAges"), t("clubPlayersCount", { n: rows.length }), new Date().toLocaleDateString(locale)].join(" · "),
      header: cols.map(([, label]) => label(t)),
      rows: rows.map((p, i) => cols.map(([, , value]) => value(p, i + 1, t))),
    });
  };

  return (
    <>
      <div className="hl-filter club-filter">
        <Seg<Gender>
          value={gender}
          options={[
            ["all", t("clubAllGenders")],
            ["M", t("men")],
            ["F", t("women")],
          ]}
          onChange={setGender}
        />
        <select className="club-age" value={age} onChange={(e) => setAge(e.target.value)} aria-label={t("clubAge")}>
          <option value="">{t("clubAllAges")}</option>
          {ages.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
        <select
          className="club-age"
          value={sortValue}
          onChange={(e) => {
            const s = SORTS.find(([k]) => k === e.target.value)!;
            setSort({ key: s[0], desc: s[2] } satisfies Sort<Key>);
          }}
          aria-label={t("clubSortBy")}
        >
          {SORTS.map(([k, label]) => (
            <option key={k} value={k}>
              {t("clubSortBy")} : {t(label)}
            </option>
          ))}
        </select>
        <button type="button" className="btn club-export" disabled={rows.length === 0} onClick={() => setExporting(true)}>
          ⤓ {t("export")}
        </button>
      </div>
      {exporting && (
        <ExportDialog
          summary={[c.name, genderLabel, age || t("clubAllAges"), t("clubPlayersCount", { n: rows.length })].filter(Boolean).join(" · ")}
          columns={EXPORT.map(([key, label, , on]) => ({ key, label: label(t), on }))}
          onExport={onExport}
          onClose={() => setExporting(false)}
        />
      )}
      <Card>
        <p className="hint small">{t("clubIntro")}</p>
        {rows.length === 0 ? (
          <p className="muted">{t("clubNone")}</p>
        ) : (
          <div className="table-wrap">
            <table className="data club-table">
              <thead>
                <tr>
                  <th className="num">#</th>
                  {th("name", t("clubPlayer"), { desc: false })}
                  {th("age", t("clubAge"), { className: "club-age-col", desc: false })}
                  {th(
                    sort.key === "classOff" ? "classOff" : "class",
                    <>
                      <span className="th-long">{t("clubOfficialToday")}</span>
                      <span className="th-short">{t("clubCategory")}</span>
                    </>,
                    { desc: false },
                  )}
                  {th(sort.key === "rankOff" ? "rankOff" : "rank", t("clubRankOfficialToday"), { className: "num club-rank-col", desc: false })}
                  {th("c", "C", { className: "num club-c" })}
                </tr>
              </thead>
              <tbody>
                {rows.map((p, i) => (
                  <tr key={p.id}>
                    <td className="num muted">{i + 1}</td>
                    <td className="club-name">
                      <a href={playerHref(p.id)}>{p.name}</a>
                      {/* on a phone: under the name, its column hidden */}
                      <small className="muted club-age-sub">{ageText(p)}</small>
                    </td>
                    <td className="muted small club-age-col">{ageText(p)}</td>
                    <td>
                      <ClassChange from={p.official?.class ?? p.today?.class} to={p.today?.class ?? p.official?.class} />
                      {/* on a phone: under the category, its column hidden */}
                      <small className="club-rank-sub">
                        <span className="muted"># </span>
                        <RankChange from={p.official?.rank} to={p.today?.rank} />
                      </small>
                    </td>
                    <td className="num club-rank-col">
                      <RankChange from={p.official?.rank} to={p.today?.rank} />
                    </td>
                    <td className="num mono club-c">{fmt3(p.today?.C ?? p.official?.C)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}

/** The club's tournaments: the coming ones (soonest first), then the past ones. */
function ClubTournaments({ club }: { club: ClubDetail }) {
  const { t, locale } = useI18n();
  const today = new Date().toISOString().slice(0, 10);
  const coming = club.tournaments.filter((x) => (x.dateEnd ?? x.dateBegin) >= today).reverse();
  const past = club.tournaments.filter((x) => (x.dateEnd ?? x.dateBegin) < today);
  const list = (rows: ClubDetail["tournaments"]) => (
    <ul className="club-tourns">
      {rows.map((x) => (
        <li key={x.id}>
          <a href={tournamentHref(x.id)}>{x.name}</a>
          <small className="muted">
            {[dateRange(x.dateBegin, x.dateEnd, locale), x.city, t("clubPlayersCount", { n: x.players })].filter(Boolean).join(" · ")}
          </small>
        </li>
      ))}
    </ul>
  );
  return (
    <Card>
      {club.tournaments.length === 0 ? (
        <p className="muted">{t("clubNoTournament")}</p>
      ) : (
        <div className="club-tourn-cols">
          <section>
            <h4>{t("whenUpcoming")}</h4>
            {coming.length ? list(coming) : <p className="muted small">{t("clubNoTournament")}</p>}
          </section>
          <section>
            <h4>{t("whenPast")}</h4>
            {past.length ? list(past) : <p className="muted small">{t("clubNoTournament")}</p>}
          </section>
        </div>
      )}
    </Card>
  );
}
