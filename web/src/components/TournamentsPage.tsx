import { lazy, Suspense, useEffect, useId, useMemo, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { api, ApiError } from "../api";
import { useAccount } from "../account";
import { trackView } from "../analytics";
import { tournamentHref, useAsync, useDebounced } from "../hooks";
import { useI18n } from "../i18n";
import { CATEGORIES, fmtDateTime, fmtInt } from "../format";
import type { AgeInfo, MePlayer, TournamentListItem, TournamentOptions, TournamentWhen } from "../types";
import { AsyncStatus, Pager, Seg, Spinner } from "./common";

// Leaflet only loads with the map
const TournamentMap = lazy(() => import("./TournamentMap"));
import { ageInfoOf, ageLabel, ageOrder, ageTitle, dateRange, eventKind, eventTitle, matchTypeLabel, rangeLabel, surfaceLabel } from "./tournamentUtil";

/** The filters, kept in the query of the URL under the names of the API's parameters. */
const KEYS = [
  "when",
  "from",
  "to",
  "q",
  "matchType",
  "age",
  "surface",
  "event",
  "fieldMin",
  "fieldMax",
  "near",
  "radius",
  "canton",
  "player",
  "sort",
  "offset",
  "view", // "map": the map instead of the list (not an API parameter)
  "openTo", // "0": "Ouverts à moi" was turned off (else the page opens with the filters of "me")
] as const;
type Key = (typeof KEYS)[number];
type Params = Partial<Record<Key, string>>;

const LIMIT = 50;
const RADII = ["10", "20", "30", "50", "100"];
const DEFAULT_RADIUS = "30";
// "field in range" quick ranges, weakest bound first
const PRESETS: [string, string][] = [
  ["R9", "R7"],
  ["R6", "R4"],
  ["R3", "R1"],
  ["N4", "N1"],
];
const WHENS: TournamentWhen[] = ["upcoming", "live", "past"];
// the filters behind "more filters" on a phone
const MORE_KEYS: Key[] = ["matchType", "age", "surface", "event", "fieldMin", "fieldMax", "near", "canton"];
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

/** The filters "Ouverts à moi" sets: the events open to "me" (MePlayer.openTo). */
const OPEN_KEYS = ["matchType", "age", "event"] as const;

function openFilters(me: MePlayer): Params {
  return Object.fromEntries(OPEN_KEYS.map((k) => [k, me.openTo[k] ?? ""]));
}

/** Whether the filters are those of "Ouverts à moi" (lists in any order). */
function isOpenToMe(p: Params, me: MePlayer): boolean {
  const norm = (v: string | undefined) => listOf(v?.toUpperCase()).sort().join(",");
  return OPEN_KEYS.every((k) => norm(p[k]) === norm(me.openTo[k] ?? ""));
}

/** The page opens with the filters of "me", unless the address has some or they were turned off. */
function initialParams(me: MePlayer | null): Params {
  const p = readParams();
  if (!me || p.openTo || OPEN_KEYS.some((k) => p[k])) return p;
  const withMe = { ...p, ...openFilters(me) };
  for (const k of KEYS) if (!withMe[k]) delete withMe[k];
  writeParams(withMe);
  return withMe;
}

/** `me`: the signed-in user's player, the "suggested" order's. */
function apiQuery(p: Params, me: number | undefined, defaultSort: string): string {
  const q = new URLSearchParams();
  for (const k of KEYS) {
    const v = p[k];
    if (!v || k === "view" || k === "openTo" || k === "sort" || (k === "radius" && !p.near)) continue;
    q.set(k, v);
  }
  const sort = p.sort || defaultSort;
  if (sort !== "suggested") q.set("sort", sort);
  else if (me) {
    q.set("sort", sort);
    q.set("suggestFor", String(me));
  }
  if (p.view === "map") q.set("places", "true");
  if (p.near && !p.radius) q.set("radius", DEFAULT_RADIUS);
  q.set("limit", String(LIMIT));
  return q.toString();
}

function whenOf(p: Params): TournamentWhen {
  return WHENS.includes(p.when as TournamentWhen) ? (p.when as TournamentWhen) : "upcoming";
}

function listOf(v: string | undefined): string[] {
  return (v ?? "").split(",").filter(Boolean);
}

export default function TournamentsPage() {
  const { t, locale, lang } = useI18n();
  const account = useAccount();
  const me = account.me;
  const [params, setParams] = useState<Params>(() => initialParams(me));
  const when = whenOf(params);
  // with a player ("me"): the tournaments suggested to him first
  const defaultSort = when === "past" ? "-date" : me ? "suggested" : "date";
  const query = useDebounced(apiQuery(params, me?.id, defaultSort), 300);
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
    // none of the filters of "me" left: a refresh does not set them again
    next.openTo = me && OPEN_KEYS.every((k) => !next[k]) ? "0" : "";
    for (const k of KEYS) if (!next[k]) delete next[k];
    setParams(next);
    writeParams(next);
  };

  const fieldFilter = !!(params.fieldMin || params.fieldMax);
  const fieldRange = rangeLabel(params.fieldMin || LEVELS[0], params.fieldMax || LEVELS[LEVELS.length - 1]);
  const offset = Number(params.offset) || 0;
  const mapView = params.view === "map";
  const data = st.data;
  // the server could not place the "near" text
  const placeError = !!params.near && st.error instanceof ApiError && st.error.status === 400 && /place|city/i.test(st.error.message);

  const setWhen = (w: TournamentWhen) => (ev: MouseEvent) => {
    if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button !== 0) return;
    ev.preventDefault();
    update({ when: w === "upcoming" ? "" : w, sort: params.sort === "date" || params.sort === "-date" ? "" : params.sort });
  };
  const whenHref = (w: TournamentWhen) => (w === "upcoming" ? "/tournaments" : `/tournaments?when=${w}`);
  const whenLabel = { upcoming: t("whenUpcoming"), live: t("whenLive"), past: t("whenPast") };

  const filtered = KEYS.some((k) => k !== "offset" && k !== "sort" && k !== "when" && k !== "view" && k !== "openTo" && params[k]);
  const reset = () => {
    const clear: Params = {};
    for (const k of KEYS) if (k !== "when" && k !== "view" && k !== "openTo") clear[k] = "";
    update(clear);
  };
  // the filters below the first row: open on a wide screen or when one is set, behind a button on a phone
  const more = MORE_KEYS.filter((k) => params[k]).length;
  const [moreOpen, setMoreOpen] = useState(() => more > 0 || !window.matchMedia?.("(max-width: 720px)").matches);
  const moreId = useId();
  const resultsId = useId();

  const ages = useMemo(() => {
    const list: AgeInfo[] = (options?.ages ?? []).map((a) => (typeof a === "number" ? (ageInfoOf(a) ?? { id: a, kind: "" }) : a));
    // an older server sends plain ids in no particular order
    if (options?.ages.some((a) => typeof a === "number")) list.sort((a, b) => ageOrder(a.id, a) - ageOrder(b.id, b));
    return list;
  }, [options]);

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

      <nav className="tabs when-tabs" aria-label={t("whenTabs")}>
        {WHENS.map((w) => (
          <a
            key={w}
            className={`tab${when === w ? " active" : ""}`}
            href={whenHref(w)}
            aria-current={when === w ? "page" : undefined}
            aria-controls={resultsId}
            onClick={setWhen(w)}
          >
            {whenLabel[w]}
          </a>
        ))}
      </nav>

      <section className="card filters" aria-label={t("filters")}>
        <div className="filter-row">
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
          <DateField label={t("dateFrom")} value={params.from} onChange={(from) => update({ from })} />
          <DateField label={t("dateTo")} value={params.to} onChange={(to) => update({ to })} />
          <Field label={t("sortBy")}>
            {(id) => (
              <select
                id={id}
                className="input-sm"
                value={params.sort ?? defaultSort}
                onChange={(e) => update({ sort: e.target.value === defaultSort ? "" : e.target.value })}
              >
                <option value="date">{t("sortDateAsc")}</option>
                <option value="-date">{t("sortDateDesc")}</option>
                <option value="players">{t("sortPlayers")}</option>
                <option value="inRange" disabled={!fieldFilter} title={fieldFilter ? undefined : t("sortInRangeHint")}>
                  {t("sortInRange")}
                </option>
                {params.near && <option value="distance">{t("sortDistance")}</option>}
                {me && (
                  <option value="suggested" title={t("suggestedHint")}>
                    {t("suggestedTitle")}
                  </option>
                )}
              </select>
            )}
          </Field>
        </div>

        {account.enabled && (
          <div className="filter-row">
            {me ? (
              <button
                className={`chip${isOpenToMe(params, me) ? " active" : ""}`}
                aria-pressed={isOpenToMe(params, me)}
                title={t("openToMeHint")}
                onClick={() => update(isOpenToMe(params, me) ? { matchType: "", age: "", event: "" } : openFilters(me))}
              >
                {t("openToMe", { who: [me.name, me.officialClass ?? me.class, me.age === "A" ? t("age_1") : me.age].filter(Boolean).join(" · ") })}
              </button>
            ) : account.user ? (
              <a className="chip" href="/account" title={t("openToMeHint")}>
                {t("openToMePick")}
              </a>
            ) : (
              <button className="chip" title={t("openToMeHint")} onClick={account.openLogin}>
                {t("openToMeSignIn")}
              </button>
            )}
          </div>
        )}

        <div className="filter-row more-toggle-row">
          <button className="btn btn-ghost more-toggle" aria-expanded={moreOpen} aria-controls={moreId} onClick={() => setMoreOpen(!moreOpen)}>
            <span className="caret" aria-hidden>
              {moreOpen ? "▾" : "▸"}
            </span>
            {t("moreFilters")}
            {more > 0 && <span className="count-badge">{more}</span>}
          </button>
        </div>

        <div id={moreId} className="filters-more" hidden={!moreOpen}>
          <div className="filter-row">
            {options && (
              <ChipGroup
                label={t("matchType")}
                ids={options.matchTypes}
                selected={params.matchType}
                name={(id) => matchTypeLabel(id, t)}
                onToggle={(id) => update({ matchType: toggle(params.matchType, String(id), true) })}
              />
            )}
            <MultiSelect
              label={t("ageCategory")}
              items={ages.map((a) => ({ value: String(a.id), label: ageLabel(a.id, t, undefined, a), title: ageTitle(a.id, t, a) }))}
              selected={params.age}
              onChange={(age) => update({ age })}
              numeric
            />
          </div>

          {(options?.surfaces?.length ?? 0) > 0 && (
            <div className="filter-row">
              <MultiSelect
                label={t("surface")}
                items={(options?.surfaces ?? []).map((s) => ({ value: s, label: surfaceLabel(s, lang) }))}
                selected={params.surface}
                onChange={(surface) => update({ surface })}
              />
            </div>
          )}

          <div className="filter-row">
            <Field label={t("eventRange")}>
              {(id) => (
                <select id={id} className="input-sm" value={params.event ?? ""} onChange={(e) => update({ event: e.target.value })}>
                  <option value="">{t("any")}</option>
                  {LEVELS.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            <RangeField
              label={t("fieldRange")}
              hint={t("fieldRangeHint")}
              min={params.fieldMin}
              max={params.fieldMax}
              onChange={(fieldMin, fieldMax) =>
                update({
                  fieldMin,
                  fieldMax,
                  sort: !fieldMin && !fieldMax && params.sort === "inRange" ? "" : params.sort,
                })
              }
            >
              <span className="range-presets" role="group" aria-label={t("fieldPresets")}>
                {PRESETS.map(([lo, hi]) => {
                  const on = params.fieldMin === lo && params.fieldMax === hi;
                  return (
                    <button key={lo} className={`chip${on ? " active" : ""}`} aria-pressed={on} onClick={() => update(on ? { fieldMin: "", fieldMax: "" } : { fieldMin: lo, fieldMax: hi })}>
                      {lo}–{hi}
                    </button>
                  );
                })}
              </span>
            </RangeField>
          </div>

          <div className="filter-row">
            <NearField
              cities={options?.cities ?? []}
              near={params.near}
              error={placeError ? t("unknownPlace") : null}
              onChange={(near) =>
                update({
                  near,
                  // a place just set sorts by distance unless another order was chosen
                  sort: !near
                    ? params.sort === "distance"
                      ? ""
                      : params.sort
                    : !params.near && !params.sort
                      ? "distance"
                      : params.sort,
                })
              }
            />
            <Field label={t("radius")}>
              {(id) => (
                <select
                  id={id}
                  className="input-sm"
                  value={params.radius ?? DEFAULT_RADIUS}
                  disabled={!params.near}
                  onChange={(e) => update({ radius: e.target.value === DEFAULT_RADIUS ? "" : e.target.value })}
                >
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
          </div>
        </div>

        {(playerId || filtered) && (
          <div className="filter-row">
            {playerId && (
              <button className="chip active" onClick={() => update({ player: "" })} aria-label={t("removePlayerFilter")}>
                {t("playerFilter", {
                  name: player.data?.name ?? `#${playerId}`,
                })}{" "}
                <span aria-hidden>×</span>
              </button>
            )}
            {filtered && (
              <button className="btn btn-ghost" onClick={reset}>
                {t("resetFilters")}
              </button>
            )}
          </div>
        )}
      </section>

      <div id={resultsId}>
        {placeError ? (
          <div className="state-screen small">
            <h3>{t("unknownPlace")}</h3>
          </div>
        ) : !data ? (
          <AsyncStatus {...st} />
        ) : (
          <div className="tourn-results" aria-busy={st.loading}>
            <div className="toolbar">
              <span className="muted">{t("tournamentsCount", { n: fmtInt(data.total, locale) })}</span>
              {st.loading && <Spinner />}
              <span className="toolbar-end">
                <Seg
                  value={mapView ? "map" : "list"}
                  options={[
                    ["list", t("viewList")],
                    ["map", t("viewMap")],
                  ]}
                  onChange={(v) => update({ view: v === "map" ? "map" : "", offset: params.offset })}
                />
              </span>
            </div>
            {mapView && data.tournaments.length > 0 ? (
              data.places ? (
                <Suspense fallback={<Spinner />}>
                  <TournamentMap places={data.places} />
                  {data.places.length < data.total && (
                    <p className="hint small">{t("mapUnplaced", { n: data.total - data.places.length })}</p>
                  )}
                </Suspense>
              ) : (
                <Spinner />
              )
            ) : data.tournaments.length === 0 ? (
              <div className="state-screen small">
                <h3>{data.updated ? t("noTournaments") : t("noTournamentData")}</h3>
                {data.updated && filtered && (
                  <button className="btn" onClick={reset}>
                    {t("resetFilters")}
                  </button>
                )}
              </div>
            ) : (
              <div className="tourn-list">
                {data.tournaments.map((tr) => (
                  <TournamentCard key={tr.id} tr={tr} fieldRange={fieldFilter ? fieldRange : null} />
                ))}
              </div>
            )}
            {!mapView && <Pager offset={offset} limit={LIMIT} total={data.total} onChange={(o) => update({ offset: String(o) })} />}
          </div>
        )}
      </div>
    </div>
  );
}

/** Adds or removes a value of a comma list (ids sorted numerically). */
function toggle(list: string | undefined, v: string, numeric = false): string {
  const set = new Set(listOf(list));
  if (set.has(v)) set.delete(v);
  else set.add(v);
  const out = [...set];
  if (numeric) out.sort((a, b) => Number(a) - Number(b));
  return out.join(",");
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
  const set = new Set(listOf(selected).map(Number));
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

/** A dropdown of checkboxes: a disclosure button, closed by Escape, a click outside or leaving it with Tab. */
function MultiSelect({
  label,
  items,
  selected,
  onChange,
  numeric = false,
}: {
  label: string;
  items: { value: string; label: string; title?: string }[];
  selected: string | undefined;
  onChange: (v: string) => void;
  numeric?: boolean;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const labelId = useId();
  const buttonId = useId();
  const panelId = useId();
  const set = new Set(listOf(selected));
  const chosen = items.filter((i) => set.has(i.value));

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [open]);

  const summary = !set.size ? t("any") : chosen.length && chosen.length <= 2 ? chosen.map((i) => i.label).join(", ") : t("selectedN", { n: set.size });

  return (
    <div
      className="field ms-field"
      ref={wrap}
      onKeyDown={(e) => {
        if (e.key === "Escape" && open) {
          e.stopPropagation();
          setOpen(false);
          button.current?.focus();
        }
      }}
      onBlur={(e) => {
        const next = e.relatedTarget as Node | null;
        if (open && next && !wrap.current?.contains(next)) setOpen(false);
      }}
    >
      <span className="field-label" id={labelId}>
        {label}
      </span>
      <button
        ref={button}
        id={buttonId}
        type="button"
        className={`input-sm ms-button${set.size ? " has-value" : ""}`}
        aria-expanded={open}
        aria-controls={panelId}
        aria-labelledby={`${labelId} ${buttonId}`}
        onClick={() => setOpen(!open)}
        disabled={!items.length && !set.size}
      >
        <span className="ms-summary">{summary}</span>
        <span className="caret" aria-hidden>
          ▾
        </span>
      </button>
      <div id={panelId} className="ms-panel" role="group" aria-labelledby={labelId} hidden={!open}>
        {items.map((i) => (
          <label key={i.value} className="ms-option" title={i.title}>
            <input type="checkbox" checked={set.has(i.value)} onChange={() => onChange(toggle(selected, i.value, numeric))} />
            <span>{i.label}</span>
          </label>
        ))}
        {set.size > 0 && (
          <button type="button" className="btn btn-ghost ms-clear" onClick={() => onChange("")}>
            {t("resetFilters")}
          </button>
        )}
      </div>
    </div>
  );
}

/** "dd.mm.yyyy" (fr) or "dd/mm/yyyy" (en) typed, or picked in the browser's calendar; the value is an ISO day. */
function DateField({ label, value, onChange }: { label: string; value: string | undefined; onChange: (iso: string) => void }) {
  const { t, lang } = useI18n();
  const id = useId();
  const errId = useId();
  const picker = useRef<HTMLInputElement>(null);
  const sep = lang === "fr" ? "." : "/";
  const show = (iso: string | undefined) => {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso ?? "");
    return m ? `${m[3]}${sep}${m[2]}${sep}${m[1]}` : "";
  };
  const [text, setText] = useState(() => show(value));
  // the value changed from outside (reset, calendar, language): show it
  useEffect(() => {
    if (parseDay(text) !== (value ?? "")) setText(show(value));
  }, [value, sep]); // eslint-disable-line react-hooks/exhaustive-deps
  const invalid = !!text.trim() && !parseDay(text);
  const change = (v: string) => {
    setText(v);
    const iso = parseDay(v);
    if (iso) {
      if (iso !== value) onChange(iso);
    } else if (!v.trim() && value) onChange("");
  };
  return (
    <div className="field date-field">
      <label htmlFor={id}>{label}</label>
      <div className="date-input">
        <input
          id={id}
          className="input-sm"
          inputMode="numeric"
          autoComplete="off"
          placeholder={t("datePlaceholder")}
          value={text}
          onChange={(e) => change(e.target.value)}
          onBlur={() => !invalid && setText(show(value))}
          aria-invalid={invalid}
          aria-describedby={invalid ? errId : undefined}
          size={10}
        />
        <button
          type="button"
          className="btn btn-ghost date-pick"
          aria-label={`${label}: ${t("openCalendar")}`}
          title={t("openCalendar")}
          onClick={() => {
            const el = picker.current;
            if (!el) return;
            try {
              el.showPicker();
            } catch {
              el.focus();
              el.click();
            }
          }}
        >
          <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.5">
            <rect x="2" y="3" width="12" height="11" rx="1.5" />
            <path d="M2 6.5h12M5.5 1.5v3M10.5 1.5v3" />
          </svg>
        </button>
        <input
          ref={picker}
          type="date"
          className="date-native"
          tabIndex={-1}
          aria-hidden
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
        />
      </div>
      {invalid && (
        <small id={errId} className="field-error">
          {t("dateInvalid")}
        </small>
      )}
    </div>
  );
}

/** "3.10.2026", "03/10/26", "2026-10-03" → "2026-10-03"; "" when not a valid date. */
function parseDay(s: string): string {
  const v = s.trim();
  let y: number, m: number, d: number;
  let r = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(v);
  if (r) [y, m, d] = [Number(r[1]), Number(r[2]), Number(r[3])];
  else {
    r = /^(\d{1,2})[./-](\d{1,2})[./-](\d{2}|\d{4})$/.exec(v);
    if (!r) return "";
    [d, m, y] = [Number(r[1]), Number(r[2]), Number(r[3])];
    if (y < 100) y += 2000;
  }
  const dt = new Date(y, m - 1, d);
  if (dt.getFullYear() !== y || dt.getMonth() !== m - 1 || dt.getDate() !== d) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${y}-${p(m)}-${p(d)}`;
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

/** Any place (postal code, town, village), resolved by the server; the cities of the data are suggested once something is typed. */
function NearField({
  cities,
  near,
  error,
  onChange,
}: {
  cities: string[];
  near: string | undefined;
  error: string | null;
  onChange: (near: string) => void;
}) {
  const { t } = useI18n();
  const id = useId();
  const errId = useId();
  const hintId = useId();
  const [text, setText] = useState(near ?? "");
  // the filter changed from outside (reset, back/forward): show it, keep what is being typed otherwise
  useEffect(() => {
    if (text.trim() !== (near ?? "")) setText(near ?? "");
  }, [near]); // eslint-disable-line react-hooks/exhaustive-deps
  const typed = text.trim();
  const suggestions = useMemo(() => {
    if (!typed) return [];
    const low = typed.toLowerCase();
    return cities.filter((c) => c.toLowerCase().includes(low)).slice(0, 12);
  }, [cities, typed]);
  const change = (v: string) => {
    setText(v);
    const p = v.trim();
    if (p.length >= 2) onChange(p);
    else if (near) onChange("");
  };
  return (
    <div className="field near-field">
      <label htmlFor={id}>{t("nearCity")}</label>
      <input
        id={id}
        className="input-sm"
        list={suggestions.length ? `${id}-cities` : undefined}
        value={text}
        autoComplete="off"
        placeholder={t("nearCityPlaceholder")}
        onChange={(e) => change(e.target.value)}
        aria-invalid={!!error}
        aria-describedby={error ? errId : hintId}
      />
      <span id={hintId} className="sr-only">
        {t("nearHint")}
      </span>
      {suggestions.length > 0 && (
        <datalist id={`${id}-cities`}>
          {suggestions.map((c) => (
            <option key={c} value={c} />
          ))}
        </datalist>
      )}
      {error && (
        <small id={errId} className="field-error" role="alert">
          {error}
        </small>
      )}
    </div>
  );
}

function TournamentCard({ tr, fieldRange }: { tr: TournamentListItem; fieldRange: string | null }) {
  const { t, locale, lang } = useI18n();
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
          {tr.surface && <span>{surfaceLabel(tr.surface, lang)}</span>}
          <span>{t("playersN", { n: fmtInt(tr.players, locale) })}</span>
          {fieldRange && tr.inRange != null && <span className="strong">{t("inRangeN", { n: fmtInt(tr.inRange, locale), range: fieldRange })}</span>}
        </div>
      </header>
      <ul className={`tourn-events${fieldRange ? " with-range" : ""}`}>
        {tr.events.map((e) => (
          // one cell per column, even when empty, so that every row lines up
          <li key={e.id}>
            <a href={tournamentHref(tr.id, e.id)} className="tourn-event-name">
              {eventTitle(e, t)}
            </a>
            <span className="tourn-event-kind muted">{eventKind(e, t)}</span>
            <span className="tourn-event-count" title={t("registeredPlayers")}>
              {t("playersN", { n: fmtInt(e.players, locale) })}
            </span>
            <span className="tourn-event-count strong">
              {fieldRange && e.inRange != null && t("inRangeN", { n: fmtInt(e.inRange, locale), range: fieldRange })}
            </span>
            <span className="tourn-event-tags">
              {e.registrationOpen && <span className="tag tag-open">{t("registrationOpen")}</span>}
              {e.hasDraw && <span className="tag tag-info">{t("hasDraw")}</span>}
            </span>
          </li>
        ))}
      </ul>
    </article>
  );
}
