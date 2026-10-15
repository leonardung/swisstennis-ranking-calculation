import { useEffect, useMemo } from "react";
import { api } from "../api";
import { trackView } from "../analytics";
import { clubHref, useAsync, useUrlState } from "../hooks";
import { useI18n } from "../i18n";
import { fmtInt } from "../format";
import type { ClubHit } from "../types";
import { AsyncStatus, Card, Pager, sortRows, useSort } from "./common";
import { ClubSearchBox } from "./SearchBox";

const PAGE = 20;

type Key = "name" | "city" | "canton" | "id" | "players";

/** Lower case without accents, for sorting by text. */
const fold = (s: string | null) => (s ?? "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();

const VALUE: Record<Key, (c: ClubHit) => number | string | null> = {
  name: (c) => fold(c.name),
  city: (c) => fold(c.city) || null,
  canton: (c) => c.canton ?? null,
  id: (c) => c.id,
  players: (c) => c.players,
};

/** A search as on the players' home page, then every club in a table sorted by any column, 20 at
 * a time. */
export default function ClubsPage() {
  const { t, locale } = useI18n();
  const st = useAsync((s) => api.clubs(s), []);
  const [offsetParam, setOffset] = useUrlState("offset", "0");
  const [sort, th] = useSort<Key>("sort", { key: "name", desc: false }, () => setOffset("0"));

  useEffect(() => {
    document.title = `${t("clubsTitle")} · ${t("appName")}`;
    trackView();
  }, [t]);

  const rows = useMemo(() => sortRows(st.data ?? [], VALUE[sort.key] ?? VALUE.name, sort.desc), [st.data, sort.key, sort.desc]);
  const offset = Math.min(Math.max(0, Number(offsetParam) || 0), Math.max(0, rows.length - 1));

  return (
    <div className="clubs-page">
      <section className="hero">
        <h1>{t("clubsHeroTitle")}</h1>
        <p className="muted">{t("clubsIntro")}</p>
        <ClubSearchBox className="hero-search" autoFocus />
      </section>
      {!st.data ? (
        <AsyncStatus {...st} />
      ) : (
        <Card title={t("clubsCount", { n: fmtInt(rows.length, locale) })}>
          <div className="table-wrap">
            <table className="data clubs-table">
              <thead>
                <tr>
                  {th("name", t("club"), { desc: false })}
                  {th("city", t("clubCity"), { desc: false })}
                  {th("canton", t("clubCanton"), { desc: false })}
                  {th("id", t("clubNumber"), { className: "num", desc: false })}
                  {th("players", t("clubPlayers"), { className: "num" })}
                </tr>
              </thead>
              <tbody>
                {rows.slice(offset, offset + PAGE).map((c) => (
                  <tr key={c.id}>
                    <td>
                      <a href={clubHref(c.id)}>{c.name}</a>
                    </td>
                    <td className="muted">{c.city}</td>
                    <td className="muted">{c.canton}</td>
                    <td className="num mono muted">{c.id}</td>
                    <td className="num">{fmtInt(c.players, locale)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager offset={offset} limit={PAGE} total={rows.length} onChange={(o) => setOffset(String(o))} />
        </Card>
      )}
    </div>
  );
}
