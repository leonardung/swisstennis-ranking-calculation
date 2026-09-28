import { useEffect, useRef, type KeyboardEvent, type ReactNode } from "react";
import { playerHref, showModal, useAsync } from "../hooks";
import { useI18n } from "../i18n";
import { fmtDate } from "../format";
import type { ListedMatch } from "../types";
import { AsyncStatus, CategoryBadge, ResultPill } from "./common";

/** What a popup lists: `key` identifies the list (a new key loads again). */
export interface MatchQuery {
  key: string;
  title: ReactNode;
  load: (signal: AbortSignal) => Promise<ListedMatch[]>;
}

/** Props that make an element open a match list on click or Enter / Space (give it the class "opens"). */
export function opens(open: () => void, title: string) {
  return {
    role: "button",
    tabIndex: 0,
    title,
    onClick: open,
    onKeyDown: (e: KeyboardEvent) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        open();
      }
    },
  };
}

/** Popup (native modal dialog) listing the matches of `query`, the latest first. */
export default function MatchListDialog({ query, onClose }: { query: MatchQuery; onClose: () => void }) {
  const { t, locale } = useI18n();
  const dialog = useRef<HTMLDialogElement>(null);
  const st = useAsync(query.load, [query.key]);
  // released on close, or when the list goes away (a click on a player: another page)
  useEffect(() => showModal(dialog.current!), []);
  const close = () => dialog.current?.close();
  return (
    <dialog
      ref={dialog}
      className="match-dialog"
      aria-labelledby="match-dialog-title"
      onClose={onClose}
      onClick={(e) => e.target === dialog.current && close()}
    >
      <div className="match-dialog-head">
        <div>
          <h2 id="match-dialog-title">{query.title}</h2>
          {st.data && <small className="muted">{t("matchCount", { n: st.data.length })}</small>}
        </div>
        <button className="btn btn-ghost" onClick={close} aria-label={t("close")}>
          ✕
        </button>
      </div>
      <div className="match-dialog-body">
        {!st.data ? (
          <AsyncStatus {...st} />
        ) : st.data.length === 0 ? (
          <p className="muted">{t("noMatches")}</p>
        ) : (
          <table className="data match-dialog-table">
            <tbody>
              {st.data.map((m, i) => (
                <tr key={i}>
                  <td className="nowrap">{fmtDate(m.date, locale)}</td>
                  <td>
                    <span className="hl-who">
                      <CategoryBadge cls={m.opponent.class} size="sm" />
                      {m.opponent.id != null ? <a href={playerHref(m.opponent.id)}>{m.opponent.name}</a> : m.opponent.name}
                    </span>
                    {m.tournament && <small className="muted match-dialog-tournament">{m.tournament}</small>}
                  </td>
                  <td className="mono nowrap">{m.score || "–"}</td>
                  <td>
                    <ResultPill result={m.result} how={m.how} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </dialog>
  );
}
