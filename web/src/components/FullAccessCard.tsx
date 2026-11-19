import { useState, type FormEvent } from "react";
import { accountApi } from "../api";
import { useAsync } from "../hooks";
import { useI18n } from "../i18n";
import { AsyncStatus, Card } from "./common";

/** Admins only: the addresses that see the juniors' names in juniors' events; add or remove
 * one (it works as soon as its owner signs in with it). */
export default function FullAccessCard() {
  const { t } = useI18n();
  const [version, setVersion] = useState(0);
  const st = useAsync((s) => accountApi.fullAccess(s), [version]);
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const change = async (run: () => Promise<void>) => {
    setBusy(true);
    setFailed(false);
    try {
      await run();
      setVersion((v) => v + 1);
      return true;
    } catch {
      setFailed(true);
      return false;
    } finally {
      setBusy(false);
    }
  };
  const add = async (e: FormEvent) => {
    e.preventDefault();
    if (await change(() => accountApi.grantFullAccess(email.trim()))) setEmail("");
  };
  return (
    <Card title={t("fullAccessTitle")}>
      <p className="muted small">{t("fullAccessIntro")}</p>
      <form className="full-access-add" onSubmit={add}>
        <input
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder={t("fullAccessPlaceholder")}
          aria-label={t("fullAccessPlaceholder")}
        />
        <button className="btn btn-primary" disabled={busy}>
          {t("fullAccessAdd")}
        </button>
      </form>
      {failed && <p className="neg small">{t("fullAccessError")}</p>}
      {!st.data ? (
        <AsyncStatus {...st} />
      ) : st.data.emails.length === 0 ? (
        <p className="hint small">{t("fullAccessNone")}</p>
      ) : (
        <ul className="fav-list">
          {st.data.emails.map((x) => (
            <li key={x.email}>
              <span>{x.email}</span>
              {!x.account && <small className="muted">{t("fullAccessNoAccount")}</small>}
              <button className="btn btn-ghost full-access-remove" disabled={busy} onClick={() => change(() => accountApi.revokeFullAccess(x.email))}>
                {t("fullAccessRemove")}
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
