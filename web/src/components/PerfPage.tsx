import { useEffect, useState, type FormEvent } from "react";
import { useI18n, type MsgKey } from "../i18n";
import { fmtInt } from "../format";
import { perfSummary, type PerfRow } from "../perf";
import { Card } from "./common";

const TOKEN_KEY = "perfToken";
const DAYS = [1, 7, 30, 90];
const KINDS: [PerfRow["kind"], MsgKey][] = [
  ["navigation", "perfNavigation"],
  ["initial", "perfInitial"],
  ["api", "perfApi"],
];

function savedToken(): string {
  try {
    return sessionStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

/** The loading times (the server's PERF_TOKEN asked once per browser session). */
export default function PerfPage() {
  const { t, locale } = useI18n();
  const [token, setToken] = useState(savedToken);
  const [draft, setDraft] = useState("");
  const [days, setDays] = useState(7);
  const [rows, setRows] = useState<PerfRow[] | null>(null);
  const [error, setError] = useState<MsgKey | null>(null);

  useEffect(() => {
    document.title = t("perfTitle");
  }, [t]);

  useEffect(() => {
    if (!token) return;
    let live = true;
    setError(null);
    perfSummary(token, days).then(
      (r) => live && setRows(r),
      (status) => {
        if (!live) return;
        setRows(null);
        if (status === 401) {
          setToken("");
          try {
            sessionStorage.removeItem(TOKEN_KEY);
          } catch {
            /* not kept */
          }
        }
        setError(status === 401 ? "perfWrongToken" : status === 404 ? "perfOff" : "perfError");
      },
    );
    return () => {
      live = false;
    };
  }, [token, days]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const v = draft.trim();
    if (!v) return;
    try {
      sessionStorage.setItem(TOKEN_KEY, v);
    } catch {
      /* kept for this page only */
    }
    setDraft("");
    setToken(v);
  };

  if (!token) {
    return (
      <Card title={t("perfTitle")}>
        <form className="perf-login" onSubmit={submit}>
          <input
            type="password"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={t("perfToken")}
            aria-label={t("perfToken")}
            autoComplete="current-password"
          />
          <button className="btn" type="submit">
            {t("perfShow")}
          </button>
        </form>
        {error && <p className="hint">{t(error)}</p>}
      </Card>
    );
  }

  return (
    <div className="perf-page">
      <Card
        title={t("perfTitle")}
        actions={
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label={t("perfDays")}>
            {DAYS.map((d) => (
              <option key={d} value={d}>
                {t("perfLastDays", { n: String(d) })}
              </option>
            ))}
          </select>
        }
      >
        <p className="hint small">{t("perfHelp")}</p>
        {error && <p className="hint">{t(error)}</p>}
      </Card>
      {rows &&
        KINDS.map(([kind, title]) => {
          const list = rows.filter((r) => r.kind === kind);
          return (
            <Card key={kind} title={t(title)}>
              {list.length === 0 ? (
                <p className="muted">{t("perfEmpty")}</p>
              ) : (
                <div className="table-wrap">
                  <table className="data">
                    <thead>
                      <tr>
                        <th>{t("perfPage")}</th>
                        <th className="num">{t("perfCount")}</th>
                        <th className="num">avg</th>
                        <th className="num">p50</th>
                        <th className="num">p95</th>
                        <th className="num">p99</th>
                      </tr>
                    </thead>
                    <tbody>
                      {list.map((r) => (
                        <tr key={r.page}>
                          <td className="mono">{r.page}</td>
                          <td className="num">{fmtInt(r.n, locale)}</td>
                          {([r.avg, r.p50, r.p95, r.p99] as number[]).map((v, i) => (
                            <td key={i} className="num">
                              {fmtInt(v, locale)} ms
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          );
        })}
    </div>
  );
}
