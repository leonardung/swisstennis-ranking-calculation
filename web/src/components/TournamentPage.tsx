import { useEffect, type ReactNode } from "react";
import { api } from "../api";
import { trackView } from "../analytics";
import { playerHref, tournamentHref, useAsync } from "../hooks";
import { useI18n } from "../i18n";
import { fmt3, fmtDate, fmtDateTime, fmtInt } from "../format";
import type { DrawMatch, DrawRound, TournamentDetail, TournamentEventDetail } from "../types";
import { AsyncStatus, Card, CategoryBadge, Hint } from "./common";
import { DrawNames, dateRange, eventKind, eventTitle, roundLabel, surfaceLabel } from "./tournamentUtil";

export default function TournamentPage({ id, eventId }: { id: number; eventId: number | null }) {
  const { t, locale } = useI18n();
  const st = useAsync((s) => api.tournament(id, s), [id]);
  const tr = st.data;

  useEffect(() => {
    if (tr) {
      document.title = `${tr.name} · ${t("appName")}`;
      trackView();
    }
  }, [tr, eventId, t]);

  if (!tr) {
    return (
      <AsyncStatus
        {...st}
        notFound={
          <div className="state-screen small">
            <h2>{t("tournamentNotFound")}</h2>
            <a className="btn" href="/tournaments">
              {t("allTournaments")}
            </a>
          </div>
        }
      />
    );
  }

  const event = tr.events.find((e) => e.id === eventId) ?? tr.events[0] ?? null;

  return (
    <div className="tournament-page">
      <section className="page-head tourn-head">
        <div>
          <a className="small" href="/tournaments">
            ‹ {t("allTournaments")}
          </a>
          <h1>{tr.name}</h1>
          <div className="muted tourn-meta">
            <span>{dateRange(tr.dateBegin, tr.dateEnd, locale)}</span>
            {tr.city && (
              <span>
                {tr.city}
                {tr.canton && ` (${tr.canton})`}
              </span>
            )}
          </div>
        </div>
        {tr.mytennisUrl && (
          <a className="btn" href={tr.mytennisUrl} target="_blank" rel="noopener noreferrer">
            {t("openMytennis")} ↗
          </a>
        )}
      </section>

      <TournamentInfo tr={tr} />

      {tr.events.length > 0 && (
        <>
          <nav className="tabs event-tabs" role="tablist" aria-label={t("events")}>
            {tr.events.map((e) => (
              <a
                key={e.id}
                role="tab"
                aria-selected={event?.id === e.id}
                className={event?.id === e.id ? "tab active" : "tab"}
                href={tournamentHref(tr.id, e.id)}
              >
                {eventTitle(e, t)}
              </a>
            ))}
          </nav>
          {event && <EventView key={event.id} event={event} />}
        </>
      )}
    </div>
  );
}

function TournamentInfo({ tr }: { tr: TournamentDetail }) {
  const { t, locale, lang } = useI18n();
  const items: [string, ReactNode][] = [];
  const add = (label: string, v: ReactNode) => {
    if (v != null && v !== "") items.push([label, v]);
  };
  add(t("surface"), tr.surface && surfaceLabel(tr.surface, lang));
  add(t("place"), tr.place);
  add(t("tournamentCategory"), tr.category);
  add(t("entryFee"), tr.entryFee);
  add(t("drawingTime"), tr.drawingTime && (tr.drawingTime.length > 10 ? fmtDateTime(tr.drawingTime, locale) : fmtDate(tr.drawingTime, locale)));
  add(t("organiser"), tr.organiser);
  add(
    t("contact"),
    (tr.email || tr.phone || tr.homePage) && (
      <span className="contact-links">
        {tr.email && <a href={`mailto:${tr.email}`}>{tr.email}</a>}
        {tr.phone && <a href={`tel:${tr.phone.replace(/\s+/g, "")}`}>{tr.phone}</a>}
        {tr.homePage && (
          <a href={/^https?:\/\//i.test(tr.homePage) ? tr.homePage : `https://${tr.homePage}`} target="_blank" rel="noopener noreferrer">
            {tr.homePage.replace(/^https?:\/\//i, "")}
          </a>
        )}
      </span>
    ),
  );
  if (!items.length && !tr.selection) return null;
  return (
    <Card className="tourn-info">
      {items.length > 0 && (
        <dl className="info-grid">
          {items.map(([label, v]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      )}
      {tr.selection && (
        <p className="small selection">
          <strong>{t("selection")}:</strong> {tr.selection}
        </p>
      )}
    </Card>
  );
}

function EventView({ event }: { event: TournamentEventDetail }) {
  const { t, locale } = useI18n();
  // a date on every match only when they differ (one date for all is the day the draw was played or updated)
  const showDate = new Set(event.draw?.rounds.flatMap((r) => r.matches.map((m) => m.date?.slice(0, 10) ?? ""))).size > 1;
  return (
    <div className="stack event-view" role="tabpanel">
      {event.draw && event.draw.rounds.length > 0 && (
        <Card title={t("draw")}>
          {event.draw.rounds.some((r) => r.round > 0) && <Bracket rounds={event.draw.rounds.filter((r) => r.round > 0)} showDate={showDate} />}
          {event.draw.rounds.some((r) => r.round === 0) && (
            <PoolMatches matches={event.draw.rounds.filter((r) => r.round === 0).flatMap((r) => r.matches)} showDate={showDate} />
          )}
        </Card>
      )}
      <Card
        title={t("registeredPlayers")}
        actions={
          <div className="event-facts muted small">
            <span>{eventKind(event, t)}</span>
            {event.dateFrom && <span>{dateRange(event.dateFrom, event.dateTo, locale)}</span>}
            {event.applicants != null && <span>{t("applicantsN", { n: fmtInt(event.applicants, locale) })}</span>}
            {event.registrationOpen && <span className="tag tag-open">{t("registrationOpen")}</span>}
            {event.pdfUrl && (
              <a href={event.pdfUrl} target="_blank" rel="noopener noreferrer">
                {t("drawPdf")} ↗
              </a>
            )}
          </div>
        }
      >
        {event.players.length === 0 ? (
          <p className="muted">{t("noRegistrations")}</p>
        ) : (
          <div className="table-wrap">
            <table className="data reg-table">
              <thead>
                <tr>
                  <th className="num hide-phone">#</th>
                  <th>{t("player")}</th>
                  <th title={t("liveClassHint")}>{t("today")}</th>
                  <th>{t("official")}</th>
                  <th className="num">
                    <Hint label="C" text={t("cColHint")} align="end" />
                  </th>
                </tr>
              </thead>
              <tbody>
                {event.players.map((p, i) => (
                  <tr key={p.personId ?? p.licence ?? i}>
                    <td className="num muted hide-phone">{i + 1}</td>
                    <th scope="row">
                      {p.personId != null ? <a href={playerHref(p.personId)}>{p.name}</a> : p.name}
                      {p.personId == null && p.licence && <small className="muted mono"> {p.licence}</small>}
                    </th>
                    <td>
                      <CategoryBadge cls={p.class} size="sm" />
                    </td>
                    <td>
                      <CategoryBadge cls={p.officialClass} size="sm" />
                    </td>
                    <td className="num">{fmt3(p.C)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

/**
 * Rounds as columns, first round to the final, on one grid so that each match sits between the two it follows from;
 * only this box scrolls sideways. A match of round r (1 = final) is at slot / 2 of its 2^(r-1) places.
 */
function Bracket({ rounds, showDate }: { rounds: DrawRound[]; showDate: boolean }) {
  const { t } = useI18n();
  const sorted = [...rounds].sort((a, b) => b.round - a.round);
  const first = sorted[0];
  const places = (r: number) => 2 ** (r - 1);
  // a first round with byes (no two matches next to each other) takes the height of the next one
  const firstIdx = new Set(first.matches.map((m) => Math.floor(m.slot / 2)));
  const compact = first.round > 1 && ![...firstIdx].some((i) => firstIdx.has(i + 1));
  const units = compact ? places(first.round) / 2 : places(first.round);
  // quarter units, so a match of a compact first round can overhang its empty neighbours by half a place
  const pad = compact ? 1 : 0;
  const rows = units * 4 + 2 * pad;
  return (
    <div className="bracket-scroll" tabIndex={0} role="region" aria-label={t("draw")}>
      <div
        className="bracket"
        style={{
          gridTemplateColumns: `repeat(${sorted.length}, var(--bracket-col))`,
          gridTemplateRows: `auto repeat(${rows}, 1fr)`,
        }}
      >
        {sorted.map((r, col) => {
          const size = (units * 4) / places(r.round);
          const span = Math.max(size, 4);
          return (
            <section key={r.round} className="bracket-round" aria-label={roundLabel(r.round, t)}>
              <h4 style={{ gridColumn: col + 1, gridRow: 1 }}>{roundLabel(r.round, t)}</h4>
              <ol className="bracket-matches">
                {[...r.matches]
                  .sort((a, b) => a.slot - b.slot)
                  .map((m) => {
                    const i = Math.floor(m.slot / 2);
                    const start = pad + i * size + (size - span) / 2;
                    return (
                      <li
                        key={m.slot}
                        style={{
                          gridColumn: col + 1,
                          gridRow: `${start + 2} / span ${span}`,
                        }}
                      >
                        <DrawMatchBox m={m} showDate={showDate} />
                      </li>
                    );
                  })}
              </ol>
            </section>
          );
        })}
      </div>
    </div>
  );
}

/** Matches outside a bracket (round 0: pools, round robin), by date. */
function PoolMatches({ matches, showDate }: { matches: DrawMatch[]; showDate: boolean }) {
  const sorted = [...matches].sort((a, b) => (a.date ?? "").localeCompare(b.date ?? "") || a.slot - b.slot);
  return (
    <ul className="pool-matches">
      {sorted.map((m) => (
        <li key={m.slot}>
          <DrawMatchBox m={m} showDate={showDate} />
        </li>
      ))}
    </ul>
  );
}

function DrawMatchBox({ m, showDate }: { m: DrawMatch; showDate: boolean }) {
  const { t, locale } = useI18n();
  // a match still to play always shows the day it is scheduled for (from the draw)
  const day = m.date && (showDate || m.winner == null) ? m.date.slice(0, 10) : null;
  return (
    <div className="draw-match">
      <div className={`draw-side${m.winner === 1 ? " won" : ""}`}>
        <DrawNames side={m.a} />
        {m.winner === 1 && <span className="sr-only"> ({t("winner")})</span>}
      </div>
      <div className={`draw-side${m.winner === 2 ? " won" : ""}`}>
        <DrawNames side={m.b} />
        {m.winner === 2 && <span className="sr-only"> ({t("winner")})</span>}
      </div>
      {(m.score || m.walkover || day) && (
        <div className="draw-score muted small">
          {m.score && <span className="mono">{m.score}</span>}
          {m.walkover && <span className="tag tag-walkover">{t("wo")}</span>}
          {day && <span>{fmtDate(day, locale)}</span>}
        </div>
      )}
    </div>
  );
}
