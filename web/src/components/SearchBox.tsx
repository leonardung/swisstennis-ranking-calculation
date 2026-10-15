import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import { clubHref, navigate, useDebounced } from "../hooks";
import { useI18n, type MsgKey } from "../i18n";
import type { ClubHit, PlayerSummary, TournamentHit } from "../types";
import { CategoryBadge } from "./common";
import { dateRange } from "./tournamentUtil";

interface Props {
  onSelect: (p: PlayerSummary) => void;
  placeholder?: string;
  autoFocus?: boolean;
  className?: string;
  /** Clear the input after a selection (header search) */
  clearOnSelect?: boolean;
  /** only players of this gender (M or F) */
  gender?: string;
}

/** Search for a player by name or licence. */
export default function SearchBox({ gender, ...props }: Props) {
  const { t } = useI18n();
  return (
    <Combobox<PlayerSummary>
      {...props}
      search={(q, signal) => api.search(q, 20, signal, gender)}
      deps={[gender]}
      placeholder={props.placeholder ?? t("searchPlaceholder")}
      noResults="searchNoResults"
      renderItem={(p) => <PlayerItem p={p} />}
    />
  );
}

function PlayerItem({ p }: { p: PlayerSummary }) {
  const { t } = useI18n();
  return (
    <>
      <span className={`gender g-${p.gender}`} title={p.gender === "F" ? t("women") : t("men")}>
        {p.gender === "F" ? "♀" : "♂"}
      </span>
      <span className="search-name">
        <strong>{p.name}</strong>
        {p.licence && <small className="muted mono">{p.licence}</small>}
      </span>
      <span className="search-cats">
        {/* no official category: today's alone, without "– →" */}
        {(p.class_official || !p.class_today) && <CategoryBadge cls={p.class_official} size="sm" />}
        {p.class_official && p.class_today && p.class_today !== p.class_official && (
          <>
            <span className="muted">→</span>
            <CategoryBadge cls={p.class_today} size="sm" />
          </>
        )}
        {!p.class_official && p.class_today && <CategoryBadge cls={p.class_today} size="sm" />}
      </span>
    </>
  );
}

/** Search for a club by name, city or number. */
export function ClubSearchBox({ className, autoFocus }: { className?: string; autoFocus?: boolean }) {
  const { t } = useI18n();
  return (
    <Combobox<ClubHit>
      className={className}
      autoFocus={autoFocus}
      onSelect={(x) => navigate(clubHref(x.id))}
      search={(q, signal) => api.clubSearch(q, 20, signal)}
      placeholder={t("searchClubPlaceholder")}
      noResults="searchNoClub"
      renderItem={(x) => <ClubItem c={x} />}
    />
  );
}

export function ClubItem({ c }: { c: ClubHit }) {
  const { t } = useI18n();
  return (
    <span className="search-name">
      <strong>{c.name}</strong>
      <small className="muted">
        {[c.city, `n° ${c.id}`, t("clubPlayersCount", { n: c.players })].filter(Boolean).join(" · ")}
      </small>
    </span>
  );
}

/** Search for a tournament by name, city or organiser. */
export function TournamentSearchBox({ onSelect, className }: { onSelect: (t: TournamentHit) => void; className?: string }) {
  const { t, locale } = useI18n();
  return (
    <Combobox<TournamentHit>
      onSelect={onSelect}
      className={className}
      search={(q, signal) => api.tournamentSearch(q, 20, signal)}
      placeholder={t("searchTournamentPlaceholder")}
      noResults="searchNoTournament"
      renderItem={(x) => (
        <span className="search-name">
          <strong>{x.name}</strong>
          <small className="muted">
            {[x.city, dateRange(x.dateBegin, x.dateEnd, locale)].filter(Boolean).join(" · ")}
          </small>
        </span>
      )}
    />
  );
}

interface ComboProps<T extends { id: number }> {
  search: (q: string, signal: AbortSignal) => Promise<T[]>;
  /** values besides the query the search depends on */
  deps?: unknown[];
  renderItem: (item: T) => ReactNode;
  onSelect: (item: T) => void;
  placeholder: string;
  noResults: MsgKey;
  autoFocus?: boolean;
  className?: string;
  clearOnSelect?: boolean;
}

/** A search input with a list of results to pick from (keyboard and mouse). */
function Combobox<T extends { id: number }>({
  search,
  deps = [],
  renderItem,
  onSelect,
  placeholder,
  noResults,
  autoFocus,
  className = "",
  clearOnSelect = true,
}: ComboProps<T>) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<T[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [busy, setBusy] = useState(false);
  const debounced = useDebounced(query.trim(), 200);
  const listId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    if (debounced.length < 2) {
      setResults([]);
      setBusy(false);
      return;
    }
    const ctrl = new AbortController();
    setBusy(true);
    search(debounced, ctrl.signal).then(
      (r) => {
        setResults(r);
        setActive(0);
        setBusy(false);
      },
      (e: Error) => {
        if (e.name !== "AbortError") {
          setResults([]);
          setBusy(false);
        }
      },
    );
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced, ...deps]);

  // close on outside click
  useEffect(() => {
    const on = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", on);
    return () => document.removeEventListener("mousedown", on);
  }, []);

  // keep active option visible
  useEffect(() => {
    const el = listRef.current?.children[active] as HTMLElement | undefined;
    el?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const choose = (p: T) => {
    onSelect(p);
    setOpen(false);
    if (clearOnSelect) {
      setQuery("");
      setResults([]);
    }
    (document.activeElement as HTMLElement | null)?.blur();
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setActive((a) => Math.min(a + 1, results.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter") {
      if (open && results[active]) {
        e.preventDefault();
        choose(results[active]);
      }
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  const showList = open && query.trim().length > 0;
  let status: string | null = null;
  if (query.trim().length < 2) status = t("searchHint");
  else if (busy && results.length === 0) status = t("searchSearching");
  else if (!busy && debounced === query.trim() && results.length === 0) status = t(noResults);

  return (
    <div className={`search ${className}`} ref={rootRef}>
      <svg className="search-icon" viewBox="0 0 24 24" aria-hidden>
        <circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" strokeWidth="2" />
        <path d="M20 20l-4-4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      </svg>
      <input
        type="search"
        role="combobox"
        aria-expanded={showList}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={showList && results[active] ? `${listId}-${active}` : undefined}
        placeholder={placeholder}
        value={query}
        autoFocus={autoFocus}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        autoComplete="off"
        spellCheck={false}
      />
      {busy && <span className="spinner search-spinner" aria-hidden />}
      {showList && (
        <div className="search-pop">
          {results.length > 0 && (
            <ul id={listId} role="listbox" ref={listRef}>
              {results.map((p, i) => (
                <li
                  key={i}
                  id={`${listId}-${i}`}
                  role="option"
                  aria-selected={i === active}
                  className={i === active ? "active" : ""}
                  onMouseEnter={() => setActive(i)}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    choose(p);
                  }}
                >
                  {renderItem(p)}
                </li>
              ))}
            </ul>
          )}
          {results.length === 0 && status && <div className="search-status muted">{status}</div>}
        </div>
      )}
    </div>
  );
}
