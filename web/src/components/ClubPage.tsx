import { useEffect } from "react";
import { api } from "../api";
import { trackView } from "../analytics";
import { CLUB_TABS, clubHref, playerHref, tournamentHref, useAsync, useUrlState, type ClubTab } from "../hooks";
import { useI18n } from "../i18n";
import { categoryIndex, fmt3 } from "../format";
import type { ClubDetail, ClubPlayer } from "../types";
import { AsyncStatus, Card, ClassChange, RankChange, Seg, sortRows, useSort } from "./common";
import { dateRange } from "./tournamentUtil";

/** "45+": 45 and older; "16&U": 16 and younger; else null (actives). */
function ageLimit(age: string | null): { senior: boolean; n: number } | null {
  const m = /^(\d+)(\+|&U)$/.exec(age ?? "");
  return m ? { senior: m[2] === "+", n: Number(m[1]) } : null;
}

/** A player can play in a team of age category `filter` (interclubs): a senior of that age or
 * older, a junior of that age or younger. */
function fits(p: ClubPlayer, filter: string): boolean {
  if (!filter) return true;
  const f = ageLimit(filter);
  const a = ageLimit(p.age);
  return !!f && !!a && f.senior === a.senior && (f.senior ? a.n >= f.n : a.n <= f.n);
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

type Key = "c" | "name" | "age" | "class" | "rank";

const fold = (s: string) => s.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase();

const VALUE: Record<Key, (p: ClubPlayer) => number | string | null | undefined> = {
  c: (p) => p.today?.C ?? p.official?.C,
  name: (p) => fold(p.name),
  age: (p) => ageOrder(p.age),
  class: (p) => {
    const i = categoryIndex(p.today?.class ?? p.official?.class);
    return i < 0 ? null : i;
  },
  rank: (p) => p.today?.rank ?? p.official?.rank,
};

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

/** The club's players of one gender and interclub age category, sortable by any column. */
function ClubPlayers({ club: c }: { club: ClubDetail }) {
  const { t } = useI18n();
  const [gender, setGender] = useUrlState("g", "M");
  const [age, setAge] = useUrlState("age", "");
  const [sort, th] = useSort<Key>("sort", { key: "c", desc: true });
  const ages = ageOptions(c.players);
  const shown = c.players.filter((p) => p.gender === gender && fits(p, age));
  const rows = sortRows(shown, VALUE[sort.key] ?? VALUE.c, sort.desc);
  return (
    <>
      <div className="hl-filter">
        <Seg
          value={gender}
          options={[
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
      </div>
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
                  {th("class", t("clubOfficialToday"), { desc: false })}
                  {th("rank", t("clubRankOfficialToday"), { className: "num", desc: false })}
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
                      <small className="muted club-age-sub">{p.age}</small>
                    </td>
                    <td className="muted small club-age-col">{p.age}</td>
                    <td>
                      <ClassChange from={p.official?.class ?? p.today?.class} to={p.today?.class ?? p.official?.class} />
                    </td>
                    <td className="num">
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
