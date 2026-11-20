import { useEffect } from "react";
import { useAccount } from "../account";
import { api } from "../api";
import { trackView } from "../analytics";
import { playerHref, useAsync, useUrlState } from "../hooks";
import { useI18n } from "../i18n";
import { CATEGORIES, fmtDate, fmtInt } from "../format";
import { AsyncStatus, Card, CategoryBadge, Field, Pager, Seg } from "./common";
import { ClubSearchBox } from "./SearchBox";

const PAGE = 50;
const ALL = "all";

/** A list (today's calculated one or a published one, every six months) of one gender, filtered
 * by birth year (the account's player's by default), age category, canton and club: each player's
 * place among those kept, 50 at a time. */
export default function RankingsPage() {
  const { t, locale } = useI18n();
  const me = useAccount().user?.me;
  const [list, setList] = useUrlState("list", "today");
  const [gender, setGender] = useUrlState("gender", me?.gender === "F" ? "F" : "M");
  const [birth, setBirth] = useUrlState("birth", ALL);
  const [cls, setCls] = useUrlState("class", ALL);
  const [age, setAge] = useUrlState("age", ALL);
  const [canton, setCanton] = useUrlState("canton", ALL);
  const [club, setClub] = useUrlState("club", "");
  const [offsetParam, setOffset] = useUrlState("offset", "0");
  const offset = Math.max(0, Number(offsetParam) || 0);
  const g = gender === "F" ? "F" : "M";

  const q: Record<string, string> = { gender: g, list, offset: String(offset), limit: String(PAGE) };
  if (birth !== ALL) q.birth = birth;
  if (age !== ALL) q.age = age;
  if (cls !== ALL) q.class = cls;
  if (canton !== ALL) q.canton = canton;
  if (club) q.club = club;
  const key = new URLSearchParams(q).toString();
  const st = useAsync((s) => api.rankings(q, s), [key]);
  // the filters keep their choices while the next page loads
  const r = st.data;
  const lists = r?.lists;
  const at = lists ? lists.indexOf(list) : -1;

  // the account's player may come after the first render: his gender is the default
  useEffect(() => {
    if (me?.gender === "F" && !new URLSearchParams(window.location.search).has("gender")) setGender("F");
  }, [me?.id]);
  // the account's player's birth year first
  const births = r?.births ?? (birth !== ALL ? [Number(birth)] : []);
  const birthOptions = me?.birth && births.includes(me.birth) ? [me.birth, ...births.filter((y) => y !== me.birth)] : births;

  useEffect(() => {
    document.title = `${t("rankingsTitle")} · ${t("appName")}`;
    trackView();
  }, [t]);

  /** a filter's change starts again from the first page */
  const filter = (set: (v: string) => void) => (v: string) => {
    setOffset("0");
    set(v);
  };

  return (
    <div className="rankings-page">
      <section className="hero">
        <h1>{t("rankingsTitle")}</h1>
        <p className="muted">{t("rankingsIntro")}</p>
      </section>
      <section className="card filters" aria-label={t("filters")}>
        <div className="filter-row">
          <Field label={t("rankingsList")} className="rankings-list-field">
            {(id) => (
              <span className="rankings-list">
                {/* the lists are the latest first: the older one is the next */}
                <button type="button" className="btn btn-ghost" disabled={!lists || at < 0 || at >= lists.length - 1} onClick={() => filter(setList)(lists![at + 1])} aria-label={t("rankingsOlder")} title={t("rankingsOlder")}>
                  ◀
                </button>
                <select id={id} className="input-sm" value={list} onChange={(e) => filter(setList)(e.target.value)}>
                  {(lists ?? [list]).map((l) => (
                    <option key={l} value={l}>
                      {l === "today" ? t("rankingsToday") : fmtDate(l, locale)}
                    </option>
                  ))}
                </select>
                <button type="button" className="btn btn-ghost" disabled={!lists || at <= 0} onClick={() => filter(setList)(lists![at - 1])} aria-label={t("rankingsNewer")} title={t("rankingsNewer")}>
                  ▶
                </button>
              </span>
            )}
          </Field>
          <div className="field">
            <span className="field-label">{t("rankingsGender")}</span>
            <Seg
              value={g}
              options={[
                ["M", t("men")],
                ["F", t("women")],
              ]}
              onChange={filter(setGender)}
            />
          </div>
          <Field label={t("rankingsBirth")}>
            {(id) => (
              <select id={id} className="input-sm" value={birth} onChange={(e) => filter(setBirth)(e.target.value)}>
                <option value={ALL}>{t("rankingsAll")}</option>
                {birthOptions.map((y) => (
                  <option key={y} value={y}>
                    {y}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <Field label={t("rankingsAgeCat")}>
            {(id) => (
              <select id={id} className="input-sm" value={age} onChange={(e) => filter(setAge)(e.target.value)}>
                <option value={ALL}>{t("rankingsAll")}</option>
                {(r?.ages ?? (age !== ALL ? [age] : [])).map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <Field label={t("category")}>
            {(id) => (
              <select id={id} className="input-sm" value={cls} onChange={(e) => filter(setCls)(e.target.value)}>
                <option value={ALL}>{t("rankingsAll")}</option>
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <Field label={t("clubCanton")}>
            {(id) => (
              <select id={id} className="input-sm" value={canton} onChange={(e) => filter(setCanton)(e.target.value)}>
                <option value={ALL}>{t("rankingsAll")}</option>
                {(r?.cantons ?? (canton !== ALL ? [canton] : [])).map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <div className="field grow">
            <span className="field-label">{t("club")}</span>
            {club ? (
              <span className="rankings-club">
                <strong>{r?.club?.name ?? `#${club}`}</strong>
                <button type="button" className="btn btn-ghost" onClick={() => filter(setClub)("")} aria-label={t("rankingsAllClubs")}>
                  ✕
                </button>
              </span>
            ) : (
              <ClubSearchBox className="rankings-club-search" placeholder={t("rankingsClubSearch")} onSelect={(c) => filter(setClub)(String(c.id))} />
            )}
          </div>
        </div>
      </section>
      {!r ? (
        <AsyncStatus {...st} />
      ) : (
        <Card title={t(g === "F" ? "rankingsCountF" : "rankingsCountM", { n: fmtInt(r.total, locale) })} className={st.loading ? "is-loading" : undefined}>
          {r.total === 0 ? (
            <p className="muted">{t("rankingsEmpty")}</p>
          ) : (
            <>
              <div className="table-wrap">
                <table className="data rankings-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>{t("player")}</th>
                      <th>{t("category")}</th>
                      <th title={r.before ? t("rankingsMoveTitle", { date: fmtDate(r.before, locale) }) : undefined}>±</th>
                      <th className="rk-col">{t("rankingsBorn")}</th>
                      <th className="rk-col">{t("rankingsAge")}</th>
                      <th className="rk-col">{t("club")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {r.players.map((p) => (
                      <tr key={p.id} className={p.id === me?.id ? "clubs-mine" : undefined}>
                        <td className="mono">{fmtInt(p.place, locale)}</td>
                        <td>
                          <a href={playerHref(p.id)}>{p.name}</a>
                        </td>
                        <td>
                          <CategoryBadge cls={p.class} size="sm" /> <span className="mono">{fmtInt(p.rank, locale)}</span>
                        </td>
                        <td>
                          <Move place={p.place} before={p.before} hasBefore={r.before != null} />
                        </td>
                        <td className="muted rk-col">{p.birth ?? ""}</td>
                        <td className="muted rk-col">{p.age ?? ""}</td>
                        <td className="muted rk-col rk-club">{p.club ?? ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="hint small">{t("rankingsHelp")}</p>
              <Pager offset={offset} limit={PAGE} total={r.total} onChange={(o) => setOffset(String(o))} />
            </>
          )}
        </Card>
      )}
    </div>
  );
}

/** ▲2 / ▼1 / = against the place on the list before; "new" when the player was not on it. */
function Move({ place, before, hasBefore }: { place: number; before: number | null; hasBefore: boolean }) {
  const { t } = useI18n();
  if (!hasBefore) return null;
  if (before == null) return <span className="rk-move rk-new">{t("rankingsNew")}</span>;
  const d = before - place;
  return <span className={`rk-move ${d > 0 ? "pos" : d < 0 ? "neg" : "muted"}`}>{d > 0 ? `▲${d}` : d < 0 ? `▼${-d}` : "="}</span>;
}
