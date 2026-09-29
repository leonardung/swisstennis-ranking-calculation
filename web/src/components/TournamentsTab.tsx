import { useId, useState, type ReactNode } from "react";
import { api } from "../api";
import { trackEvent } from "../analytics";
import { playerHref, tournamentHref, useAsync } from "../hooks";
import { useI18n } from "../i18n";
import { fmtChance, fmtInt, fmt3 } from "../format";
import type { PlayerTournamentEntry } from "../types";
import { AsyncStatus, Card, CategoryBadge, Delta, Hint, ResultPill } from "./common";
import { DrawNames, dateRange, eventKind, eventTitle, isoDay, isSingles, roundLabel } from "./tournamentUtil";

/** The tournaments of a player on the tournaments page: the last year and what is coming. */
function playerTournamentsHref(id: number): string {
  const from = new Date();
  from.setFullYear(from.getFullYear() - 1);
  return `/tournaments?player=${id}&from=${isoDay(from)}&sort=-date`;
}

export default function TournamentsTab({ playerId }: { playerId: number }) {
  const { t, locale } = useI18n();
  const st = useAsync((s) => api.playerTournaments(playerId, s), [playerId]);
  const d = st.data;
  if (!d) return <AsyncStatus {...st} />;

  return (
    <div className="stack player-tournaments">
      <Card
        title={t("registeredTitle")}
        actions={
          <a className="btn btn-ghost" href={playerTournamentsHref(playerId)}>
            {t("allTournaments")}
          </a>
        }
      >
        {d.registered.length === 0 ? (
          <p className="muted">{t("noRegistered")}</p>
        ) : (
          <ul className="entry-list">
            {d.registered.map((r) => (
              <li key={r.event.id}>
                <EntryHead entry={r} />
                {isSingles(r.event.matchType) && <OutlookToggle playerId={playerId} eventId={r.event.id} />}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title={<Hint label={t("suggestedTitle")} text={t("suggestedHint")} />}>
        {d.suggested.length === 0 ? (
          <p className="muted">{t("noSuggested")}</p>
        ) : (
          <ul className="entry-list">
            {d.suggested.map((r) => (
              <li key={r.event.id}>
                <EntryHead entry={r} openTag={false} side={<span className="strong">{t("nearLevelN", { n: fmtInt(r.inRange, locale) })}</span>} />
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title={t("pastTitle")}>
        {d.past.length === 0 ? (
          <p className="muted">{t("noPast")}</p>
        ) : (
          <ul className="entry-list">
            {d.past.map((r) => {
              const won = r.results.some((x) => x.round === 1 && x.win);
              return (
                <li key={r.event.id}>
                  <EntryHead
                    entry={r}
                    side={
                      r.reached != null || won ? (
                        <span className="tag tag-ok">{won ? t("wonTournament") : t("reachedRound", { round: roundLabel(r.reached as number, t) })}</span>
                      ) : undefined
                    }
                  />
                  {r.results.length > 0 && (
                    <div className="table-wrap">
                      <table className="data past-results">
                        <tbody>
                          {[...r.results]
                            .sort((a, b) => b.round - a.round)
                            .map((x, i) => (
                              <tr key={i}>
                                <td className="muted nowrap">{x.round > 0 ? roundLabel(x.round, t) : ""}</td>
                                <td>
                                  <DrawNames side={x.opponent} />
                                </td>
                                <td className="nowrap">
                                  {x.win != null && <ResultPill result={x.win ? "win" : "loss"} how={x.walkover ? "walkover" : "normal"} />}
                                </td>
                                <td className="mono nowrap">{x.score ?? ""}</td>
                              </tr>
                            ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </div>
  );
}

function EntryHead({ entry, side, openTag = true }: { entry: PlayerTournamentEntry; side?: ReactNode; openTag?: boolean }) {
  const { t, locale } = useI18n();
  const { tournament: tr, event: e } = entry;
  return (
    <div className="entry-head">
      <div className="entry-main">
        <a href={tournamentHref(tr.id, e.id)} className="strong">
          {tr.name}
        </a>
        <small className="muted">
          {eventTitle(e, t)} · {eventKind(e, t)}
        </small>
        <small className="muted">
          {dateRange(tr.dateBegin, tr.dateEnd, locale)}
          {tr.city && ` · ${tr.city}`}
          {tr.canton && ` (${tr.canton})`}
          {` · ${t("playersN", { n: fmtInt(e.players, locale) })}`}
        </small>
      </div>
      <div className="entry-side">
        {openTag && e.registrationOpen && <span className="tag tag-open">{t("registrationOpen")}</span>}
        {side}
      </div>
    </div>
  );
}

function OutlookToggle({ playerId, eventId }: { playerId: number; eventId: number }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div className="outlook">
      <button
        className="btn btn-ghost outlook-toggle"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => {
          if (!open) trackEvent("outlook");
          setOpen(!open);
        }}
      >
        <span className="caret" aria-hidden>
          {open ? "▾" : "▸"}
        </span>
        {t("outlook")}
      </button>
      <div id={id}>{open && <OutlookPanel playerId={playerId} eventId={eventId} />}</div>
    </div>
  );
}

function OutlookPanel({ playerId, eventId }: { playerId: number; eventId: number }) {
  const { t, locale } = useI18n();
  const st = useAsync((s) => api.outlook(playerId, eventId, s), [playerId, eventId]);
  const o = st.data;
  if (!o) return <AsyncStatus {...st} />;
  return (
    <div className="outlook-panel">
      <div className="summary-bar">
        <div className="summary-item">
          <small>{t("fieldCount")}</small>
          <strong>{fmtInt(o.field.count, locale)}</strong>
        </div>
        <div className="summary-item">
          <small>
            <Hint label={t("fieldMedianW")} text={t("wColHint")} />
          </small>
          <strong>{fmt3(o.field.medianW)}</strong>
        </div>
        <div className="summary-item">
          <small>{t("fieldTopW")}</small>
          <strong>{fmt3(o.field.topW)}</strong>
        </div>
        <div className="summary-item">
          <small>
            <Hint label={t("expectedGain")} text={t("expectedGainHint")} />
          </small>
          <strong>
            <Delta value={o.expected?.gain ?? null} />
          </strong>
        </div>
      </div>

      {o.expected && o.expected.rounds.length > 0 && (
        <div className="table-wrap">
          <table className="data outlook-rounds">
            <thead>
              <tr>
                <th>{t("winsInARow")}</th>
                <th className="num">{t("reachChance")}</th>
                <th className="num">{t("gainC")}</th>
              </tr>
            </thead>
            <tbody>
              {o.expected.rounds.map((r) => (
                <tr key={r.round}>
                  <td>{r.round}</td>
                  <td className="num">{fmtChance(r.reach)}</td>
                  <td className="num">
                    <Delta value={r.gain} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {o.opponents.length === 0 ? (
        <p className="muted">{t("noOpponents")}</p>
      ) : (
        <div className="table-wrap">
          <table className="data outlook-opps">
            <thead>
              <tr>
                <th>{t("opponent")}</th>
                <th className="num hide-phone">
                  <Hint label="W" text={t("wColHint")} align="end" />
                </th>
                <th className="num">{t("winChance")}</th>
                <th className="num">{t("ifWin")}</th>
                <th className="num">{t("ifLoss")}</th>
              </tr>
            </thead>
            <tbody>
              {o.opponents.map((p, i) => (
                <tr key={p.personId ?? i}>
                  <th scope="row">
                    {p.personId != null ? <a href={playerHref(p.personId)}>{p.name}</a> : p.name} <CategoryBadge cls={p.class} size="sm" />
                  </th>
                  <td className="num hide-phone">{fmt3(p.W)}</td>
                  <td className="num">{p.chance != null ? fmtChance(p.chance) : "–"}</td>
                  <td className="num">
                    <Delta value={p.gainWin} />
                  </td>
                  <td className="num">
                    <Delta value={p.lossLoss} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
