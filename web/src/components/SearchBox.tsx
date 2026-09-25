import { useEffect, useId, useRef, useState } from "react";
import { api } from "../api";
import { useDebounced } from "../hooks";
import { useI18n } from "../i18n";
import type { PlayerSummary } from "../types";
import { CategoryBadge } from "./common";

interface Props {
  onSelect: (p: PlayerSummary) => void;
  placeholder?: string;
  autoFocus?: boolean;
  className?: string;
  /** Clear the input after a selection (header search) */
  clearOnSelect?: boolean;
}

export default function SearchBox({ onSelect, placeholder, autoFocus, className = "", clearOnSelect = true }: Props) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<PlayerSummary[]>([]);
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
    api.search(debounced, 20, ctrl.signal).then(
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
  }, [debounced]);

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

  const choose = (p: PlayerSummary) => {
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
  else if (!busy && debounced === query.trim() && results.length === 0) status = t("searchNoResults");

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
        placeholder={placeholder ?? t("searchPlaceholder")}
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
                  key={p.id}
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
                  <span className={`gender g-${p.gender}`} title={p.gender === "F" ? t("women") : t("men")}>
                    {p.gender === "F" ? "♀" : "♂"}
                  </span>
                  <span className="search-name">
                    <strong>{p.name}</strong>
                    {p.licence && <small className="muted mono">{p.licence}</small>}
                  </span>
                  <span className="search-cats">
                    <CategoryBadge cls={p.class_official} size="sm" />
                    {p.class_today && p.class_today !== p.class_official && (
                      <>
                        <span className="muted">→</span>
                        <CategoryBadge cls={p.class_today} size="sm" />
                      </>
                    )}
                  </span>
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
