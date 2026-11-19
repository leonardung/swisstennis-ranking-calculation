import { useEffect } from "react";
import { useAccount } from "../account";
import { api } from "../api";
import { trackView } from "../analytics";
import { playerHref, useAsync, useUrlState } from "../hooks";
import { useI18n } from "../i18n";
import { fmtInt } from "../format";
import { AsyncStatus, Card, Pager, Seg, Standing } from "./common";

const PAGE = 50;

/** The players born the same year (a birth year and a gender): on the last list of each year,
 * their place among them, category and rank; the best on the latest list first, 50 at a time. */
export default function CohortsPage() {
  const { t, locale } = useI18n();
  const [year, setYear] = useUrlState("year", "");
  const [gender, setGender] = useUrlState("gender", "M");
  const [offsetParam, setOffset] = useUrlState("offset", "0");
  const offset = Math.max(0, Number(offsetParam) || 0);
  const g = gender === "F" ? "F" : "M";
  const st = useAsync((s) => api.cohort({ year, gender: g, offset, limit: PAGE }, s), [year, g, offset]);
  const me = useAccount().user?.me?.id;
  const c = st.data;

  useEffect(() => {
    document.title = `${t("cohortsTitle")} · ${t("appName")}`;
    trackView();
  }, [t]);

  return (
    <div className="cohorts-page">
      <section className="hero">
        <h1>{t("cohortsTitle")}</h1>
        <p className="muted">{t("cohortsIntro")}</p>
      </section>
      <div className="hl-filter">
        <Seg
          value={g}
          options={[
            ["M", t("men")],
            ["F", t("women")],
          ]}
          onChange={(v) => {
            setOffset("0");
            setGender(v);
          }}
        />
        <select
          className="hl-age"
          aria-label={t("cohortsYear")}
          value={c?.year ?? year}
          onChange={(e) => {
            setOffset("0");
            setYear(e.target.value);
          }}
        >
          {(c?.years ?? []).map((y) => (
            <option key={y} value={y}>
              {y}
            </option>
          ))}
        </select>
      </div>
      {!c ? (
        <AsyncStatus {...st} />
      ) : (
        <Card title={t(c.gender === "F" ? "cohortsCountF" : "cohortsCountM", { n: fmtInt(c.total, locale), year: c.year })}>
          {c.total === 0 ? (
            <p className="muted">{t("cohortsEmpty")}</p>
          ) : (
            <>
              <div className="table-wrap">
                <table className="data cohort-table">
                  <thead>
                    <tr>
                      <th>{t("player")}</th>
                      {c.dates.map((d, i) => (
                        <th key={d} className="num" title={t("cohortsOf", { n: fmtInt(c.of[i], locale) })}>
                          {d.slice(0, 4)}
                          <small className="muted cohort-of">/{fmtInt(c.of[i], locale)}</small>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {c.players.map((p) => (
                      <tr key={p.id} className={p.id === me ? "clubs-mine" : undefined}>
                        <td className="cohort-name">
                          <a href={playerHref(p.id)}>{p.name}</a>
                        </td>
                        {p.ranks.map((r, i) => (
                          <td key={c.dates[i]} className="num cohort-cell">
                            {r && (
                              <>
                                <strong>{fmtInt(r.peer, locale)}</strong>
                                <Standing cls={r.class} rank={r.rank} />
                              </>
                            )}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="hint small">{t("cohortsHelp")}</p>
              <Pager offset={offset} limit={PAGE} total={c.total} onChange={(o) => setOffset(String(o))} />
            </>
          )}
        </Card>
      )}
    </div>
  );
}
