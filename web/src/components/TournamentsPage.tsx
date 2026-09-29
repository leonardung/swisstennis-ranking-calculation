import { useEffect, useId, useMemo, useState, type ReactNode } from "react";
import { api } from "../api";
import { trackView } from "../analytics";
import { tournamentHref, useAsync, useDebounced } from "../hooks";
import { useI18n } from "../i18n";
import { CATEGORIES, fmtDateTime, fmtInt } from "../format";
import type { TournamentListItem, TournamentOptions } from "../types";
import { AsyncStatus, Spinner } from "./common";
import { ageLabel, dateRange, eventKind, eventTitle, isoDay, matchTypeLabel } from "./tournamentUtil";

/** The filters, kept in the query of the URL under the names of the API's parameters. */
const KEYS = [
  "from",
  "to",
  "q",
  "matchType",
  "age",
  "eventMin",
  "eventMax",
  "fieldMin",
  "fieldMax",
  "near",
  "radius",
  "canton",
  "player",
  "sort",
  "offset",
] as const;
type Key = (typeof KEYS)[number];
type Params = Partial<Record<Key, string>>;

const LIMIT = 50;
const RADII = ["10", "20", "30", "50", "100"];
const DEFAULT_RADIUS = "30";
// "field in range" preset: the players of the upper regional and lower national levels
const PRESET = { fieldMin: "R3", fieldMax: "N4" };
const RECENT_DAYS = 60;
// weakest first: a range reads "R9 … N1"
const LEVELS = [...CATEGORIES].reverse();

function readParams(): Params {
  const q = new URLSearchParams(window.location.search);
  const p: Params = {};
  for (const k of KEYS) {
    const v = q.get(k);
    if (v) p[k] = v;
  }
  return p;
}

function writeParams(p: Params): void {
  const url = new URL(window.location.href);
  for (const k of KEYS) {
    if (p[k]) url.searchParams.set(k, p[k] as string);
    else url.searchParams.delete(k);
  }
  window.history.replaceState(window.history.state, "", url);
}

function apiQuery(p: Params): string {
  const q = new URLSearchParams();
  for (const k of KEYS) {
    const v = p[k];
    if (!v || (k === "radius" && !p.near)) continue;
    q.set(k, v);
  }
  if (p.near && !p.radius) q.set("radius", DEFAULT_RADIUS);
  q.set("limit", String(LIMIT));
  return q.toString();
}

function toggleId(list: string | undefined, id: number): string {
  const ids = new Set((list ?? "").split(",").filter(Boolean));
  const k = String(id);
  if (ids.has(k)) ids.delete(k);
  else ids.add(k);
  return [...ids].sort((a, b) => Number(a) - Number(b)).join(",");
}

export default function TournamentsPage() {
  const { t, locale } = useI18n();
  const [params, setParams] = useState<Params>(readParams);
  const query = useDebounced(apiQuery(params), 300);
  const st = useAsync((s) => api.tournaments(query, s), [query]);
  const [options, setOptions] = useState<TournamentOptions | null>(null);
  if (st.data && st.data.options !== options) setOptions(st.data.options);
  const playerId = Number(params.player) || null;
  const player = useAsync(playerId ? (s) => api.player(playerId, s) : null, [playerId]);

  useEffect(() => {
    document.title = `${t("tournamentsTitle")} · ${t("appName")}`;
    trackView();
  }, [t]);

  /** Changes filters; any change but paging goes back to the first page. */
  const update = (patch: Params) => {
    const next: Params = { ...params, ...patch };
    if (!("offset" in patch)) delete next.offset;
    for (const k of KEYS) if (!next[k]) delete next[k];
    setParams(next);
    writeParams(next);
  };

  const fieldFilter = !!(params.fieldMin || params.fieldMax);
  const upcoming = !params.from && !params.to;
  const recent = !!params.to && params.sort === "-date";
  const offset = Number(params.offset) || 0;
  const today = isoDay(new Date());
  const data = st.data;

  const setRecent = () => {
    const from = new Date();
    from.setDate(from.getDate() - RECENT_DAYS);
    update({ from: isoDay(from), to: today, sort: "-date" });
  };

  const filtered = KEYS.some((k) => k !== "offset" && k !== "sort" && params[k]);

  return (
    <div className="tournaments-page">
      <div className="page-head">
        <h1>{t("tournamentsTitle")}</h1>
        {data?.updated && (
          <span className="muted small">
            {t("tournamentsUpdated")} {fmtDateTime(data.updated, locale)}
          </span>
        )}
      </div>

      <section className="card filters" aria-label={t("filters")}>
        <div className="filter-row">
          <div className="seg" role="group" aria-label={t("when")}>
            <button className={upcoming ? "active" : ""} aria-pressed={upcoming} onClick={() => update({ from: "", to: "", sort: "" })}>
              {t("whenUpcoming")}
            </button>
            <button className={recent ? "active" : ""} aria-pressed={recent} onClick={setRecent}>
              {t("whenRecent")}
            </button>
          </div>
          <Field label={t("dateFrom")}>
            {(id) => (
              <input id={id} type="date" className="input-sm" value={params.from ?? today} onChange={(e) => update({ from: e.target.value })} />
            )}
          </Field>
          <Field label={t("dateTo")}>
            {(id) => <input id={id} type="date" className="input-sm" value={params.to ?? ""} onChange={(e) => update({ to: e.target.value })} />}
          </Field>
          <Field label={t("searchText")} grow>
            {(id) => (
              <input
                id={id}
                type="search"
                className="input-sm"
                placeholder={t("searchTournaments")}
                value={params.q ?? ""}
                onChange={(e) => update({ q: e.target.value })}
              />
            )}
          </Field>
        </div>

        {options && (
          <>
            <ChipGroup
              label={t("matchType")}
              ids={options.matchTypes}
              selected={params.matchType}
              name={(id) => matchTypeLabel(id, t)}
              onToggle={(id) => update({ matchType: toggleId(params.matchType, id) })}
            />
            <ChipGroup
              label={t("ageCategory")}
              ids={options.ages}
              selected={params.age}
              name={(id) => ageLabel(id, t)}
              onToggle={(id) => update({ age: toggleId(params.age, id) })}
            />
          </>
        )}

        <div className="filter-row">
          <RangeField
            label={t("eventRange")}
            min={params.eventMin}
            max={params.eventMax}
            onChange={(eventMin, eventMax) => update({ eventMin, eventMax })}
          />
          <RangeField
            label={t("fieldRange")}
            hint={t("fieldRangeHint")}
            min={params.fieldMin}
            max={params.fieldMax}
            onChange={(fieldMin, fieldMax) => update({ fieldMin, fieldMax, sort: !fieldMin && !fieldMax && params.sort === "inRange" ? "" : params.sort })}
          >
            <button
              className={`chip${params.fieldMin === PRESET.fieldMin && params.fieldMax === PRESET.fieldMax ? " active" : ""}`}
              onClick={() => update(PRESET)}
              aria-pressed={params.fieldMin === PRESET.fieldMin && params.fieldMax === PRESET.fieldMax}
            >
              {PRESET.fieldMin}–{PRESET.fieldMax}
            </button>
          </RangeField>
        </div>

        <div className="filter-row">
          <NearField
            cities={options?.cities ?? []}
            near={params.near}
            onChange={(near) => update({ near, sort: !near && params.sort === "distance" ? "" : params.sort })}
          />
          <Field label={t("radius")}>
            {(id) => (
              <select id={id} className="input-sm" value={params.radius ?? DEFAULT_RADIUS} disabled={!params.near} onChange={(e) => update({ radius: e.target.value })}>
                {RADII.map((r) => (
                  <option key={r} value={r}>
                    {r} km
                  </option>
                ))}
              </select>
            )}
          </Field>
          <Field label={t("canton")}>
            {(id) => (
              <select id={id} className="input-sm" value={params.canton ?? ""} onChange={(e) => update({ canton: e.target.value })}>
                <option value="">{t("any")}</option>
                {(options?.cantons ?? []).map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <Field label={t("sortBy")}>
            {(id) => (
              <select id={id} className="input-sm" value={params.sort ?? "date"} onChange={(e) => update({ sort: e.target.value === "date" ? "" : e.target.value })}>
                <option value="date">{t("sortDateAsc")}</option>
                <option value="-date">{t("sortDateDesc")}</option>
                <option value="players">{t("sortPlayers")}</option>
                {fieldFilter && <option value="inRange">{t("sortInRange")}</option>}
                {params.near && <option value="distance">{t("sortDistance")}</option>}
              </select>
            )}
          </Field>
        </div>

        {(playerId || filtered) && (
          <div className="filter-row">
            {playerId && (
              <button className="chip active" onClick={() => update({ player: "" })} aria-label={t("removePlayerFilter")}>
                {t("playerFilter", { name: player.data?.name ?? `#${playerId}` })} <span aria-hidden>×</span>
              </button>
            )}
            {filtered && (
              <button className="btn btn-ghost" onClick={() => update({ from: "", to: "", q: "", matchType: "", age: "", eventMin: "", eventMax: "", fieldMin: "", fieldMax: "", near: "", radius: "", canton: "", player: "", sort: "" })}>
                {t("resetFilters")}
              </button>
            )}
          </div>
        )}
      </section>

      {!data ? (
        <AsyncStatus {...st} />
      ) : (
        <div className="tourn-results" aria-busy={st.loading}>
          <div className="toolbar">
            <span className="muted">{t("tournamentsCount", { n: fmtInt(data.total, locale) })}</span>
            {st.loading && <Spinner />}
          </div>
          {data.tournaments.length === 0 ? (
            <div className="state-screen small">
              <h3>{data.updated ? t("noTournaments") : t("noTournamentData")}</h3>
            </div>
          ) : (
            <div className="tourn-list">
              {data.tournaments.map((tr) => (
                <TournamentCard key={tr.id} tr={tr} showInRange={fieldFilter} />
              ))}
            </div>
          )}
          {data.total > LIMIT && (
            <nav className="pager" aria-label={t("pages")}>
              <button className="btn" disabled={offset <= 0} onClick={() => update({ offset: String(Math.max(0, offset - LIMIT)) })}>
                ‹ {t("previous")}
              </button>
              <span className="muted">
                {fmtInt(offset + 1, locale)}–{fmtInt(Math.min(offset + LIMIT, data.total), locale)} / {fmtInt(data.total, locale)}
              </span>
              <button className="btn" disabled={offset + LIMIT >= data.total} onClick={() => update({ offset: String(offset + LIMIT) })}>
                {t("next")} ›
              </button>
            </nav>
          )}
        </div>
      )}
    </div>
  );
}

function Field({ label, grow = false, children }: { label: string; grow?: boolean; children: (id: string) => ReactNode }) {
  const id = useId();
  return (
    <div className={`field${grow ? " grow" : ""}`}>
      <label htmlFor={id}>{label}</label>
      {children(id)}
    </div>
  );
}

function ChipGroup({
  label,
  ids,
  selected,
  name,
  onToggle,
}: {
  label: string;
  ids: number[];
  selected: string | undefined;
  name: (id: number) => string;
  onToggle: (id: number) => void;
}) {
  const set = new Set((selected ?? "").split(",").filter(Boolean).map(Number));
  if (!ids.length) return null;
  return (
    <div className="chip-filter" role="group" aria-label={label}>
      <span className="field-label">{label}</span>
      <div className="chips">
        {ids.map((id) => (
          <button key={id} className={`chip${set.has(id) ? " active" : ""}`} aria-pressed={set.has(id)} onClick={() => onToggle(id)}>
            {name(id)}
          </button>
        ))}
      </div>
    </div>
  );
}

/** Two category selects, weakest first ("R3 … N4"); both empty = any. */
function RangeField({
  label,
  hint,
  min,
  max,
  onChange,
  children,
}: {
  label: string;
  hint?: string;
  min: string | undefined;
  max: string | undefined;
  onChange: (min: string, max: string) => void;
  children?: ReactNode;
}) {
  const { t } = useI18n();
  const id = useId();
  return (
    <div className="field range-field" role="group" aria-labelledby={id}>
      <span className="field-label" id={id} title={hint}>
        {label}
      </span>
      <div className="range-inputs">
        <select className="input-sm" aria-label={`${label}: ${t("rangeFrom")}`} value={min ?? ""} onChange={(e) => onChange(e.target.value, max ?? "")}>
          <option value="">{t("any")}</option>
          {LEVELS.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <span aria-hidden>–</span>
        <select className="input-sm" aria-label={`${label}: ${t("rangeTo")}`} value={max ?? ""} onChange={(e) => onChange(min ?? "", e.target.value)}>
          <option value="">{t("any")}</option>
          {LEVELS.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        {children}
      </div>
      {hint && <small className="muted">{hint}</small>}
    </div>
  );
}

/** A city typed with suggestions; only a known city is used as filter. */
function NearField({ cities, near, onChange }: { cities: string[]; near: string | undefined; onChange: (near: string) => void }) {
  const { t } = useI18n();
  const id = useId();
  const [text, setText] = useState(near ?? "");
  const byName = useMemo(() => new Map(cities.map((c) => [c.toLowerCase(), c])), [cities]);
  // the filter changed from outside (reset, back/forward): show it, keep what is being typed otherwise
  useEffect(() => {
    if ((byName.get(text.trim().toLowerCase()) ?? "") !== (near ?? "")) setText(near ?? "");
  }, [near]); // eslint-disable-line react-hooks/exhaustive-deps
  const change = (v: string) => {
    setText(v);
    const city = byName.get(v.trim().toLowerCase());
    if (city) onChange(city);
    else if (!v.trim() || near) onChange("");
  };
  return (
    <div className="field">
      <label htmlFor={id}>{t("nearCity")}</label>
      <input
        id={id}
        className="input-sm"
        list={`${id}-cities`}
        value={text}
        placeholder={t("nearCityPlaceholder")}
        onChange={(e) => change(e.target.value)}
        aria-invalid={!!text.trim() && !near}
      />
      <datalist id={`${id}-cities`}>
        {cities.map((c) => (
          <option key={c} value={c} />
        ))}
      </datalist>
    </div>
  );
}

function TournamentCard({ tr, showInRange }: { tr: TournamentListItem; showInRange: boolean }) {
  const { t, locale } = useI18n();
  return (
    <article className="card tourn-card">
      <header className="tourn-card-head">
        <h3>
          <a href={tournamentHref(tr.id)}>{tr.name}</a>
        </h3>
        <div className="muted small tourn-meta">
          <span>{dateRange(tr.dateBegin, tr.dateEnd, locale)}</span>
          {tr.city && (
            <span>
              {tr.city}
              {tr.canton && ` (${tr.canton})`}
            </span>
          )}
          {tr.distanceKm != null && <span>{t("distanceKm", { km: Math.round(tr.distanceKm) })}</span>}
          <span>{t("playersN", { n: fmtInt(tr.players, locale) })}</span>
          {showInRange && tr.inRange != null && <span className="strong">{t("inRangeN", { n: fmtInt(tr.inRange, locale) })}</span>}
        </div>
      </header>
      <ul className="tourn-events">
        {tr.events.map((e) => (
          <li key={e.id}>
            <a href={tournamentHref(tr.id, e.id)} className="tourn-event-name">
              {eventTitle(e, t)}
            </a>
            <small className="muted">{eventKind(e, t)}</small>
            <span className="tourn-event-side">
              <span title={t("registeredPlayers")}>{t("playersN", { n: fmtInt(e.players, locale) })}</span>
              {showInRange && e.inRange != null && <span className="strong">{t("inRangeN", { n: fmtInt(e.inRange, locale) })}</span>}
              {e.registrationOpen && <span className="tag tag-open">{t("registrationOpen")}</span>}
              {e.hasDraw && <span className="tag tag-info">{t("hasDraw")}</span>}
            </span>
          </li>
        ))}
      </ul>
    </article>
  );
}
