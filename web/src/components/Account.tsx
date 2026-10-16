import { useEffect, useRef, useState, type FormEvent } from "react";
import { accountApi, ApiError } from "../api";
import type { User } from "../types";
import { useAccount } from "../account";
import { trackEvent } from "../analytics";
import { navigate, playerHref, showModal, useAsync } from "../hooks";
import { useI18n } from "../i18n";
import { fmt3, fmtDate } from "../format";
import { AsyncStatus, Card, CategoryBadge, ClassChange, Spinner } from "./common";
import { MatchTable } from "./MatchListDialog";
import SearchBox from "./SearchBox";

const PASSWORD_MIN = 8; // accounts.PASSWORD_MIN

type Mode = "login" | "signup" | "forgot";
type SendStatus = "idle" | "sending" | "sent" | "error" | "limited" | "wrong" | "unverified";

const GOOGLE_ICON = (
  <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden>
    <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.6 5.4 2.6 13.2l7.9 6.2C12.4 13.6 17.7 9.5 24 9.5z" />
    <path fill="#4285F4" d="M46.1 24.6c0-1.6-.1-3.1-.4-4.6H24v9h12.4c-.5 2.9-2.2 5.3-4.6 6.9l7.4 5.8c4.3-4 6.9-9.9 6.9-17.1z" />
    <path fill="#FBBC05" d="M10.5 28.6A14.5 14.5 0 0 1 9.5 24c0-1.6.3-3.2.8-4.6l-7.9-6.2A24 24 0 0 0 0 24c0 3.9.9 7.5 2.6 10.8l7.9-6.2z" />
    <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.4-5.8c-2.1 1.4-4.8 2.3-8.5 2.3-6.3 0-11.6-4.1-13.5-9.8l-7.9 6.2C6.6 42.6 14.6 48 24 48z" />
  </svg>
);

/** Once signed in: the first time to the account page (pick "me"), else to `next`. */
async function afterSignIn(refresh: () => Promise<User | null>, next: string) {
  const user = await refresh();
  trackEvent("login");
  navigate(user && !user.me ? "/account" : next);
}

/** Sign in with email and password or Google; create an account (the password works once the
 * email is verified by a link); ask for a link to choose a new password. */
function LoginForm({ next, autoFocus = false, onSignedIn }: { next: string; autoFocus?: boolean; onSignedIn?: () => void }) {
  const { t, lang } = useI18n();
  const account = useAccount();
  const [mode, setMode] = useState<Mode>("login");
  const [status, setStatus] = useState<SendStatus>("idle");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const switchTo = (m: Mode) => {
    setMode(m);
    setStatus("idle");
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setStatus("sending");
    const address = email.trim();
    try {
      if (mode === "login") {
        await accountApi.login(address, password, lang);
        onSignedIn?.();
        await afterSignIn(account.refresh, next);
        return;
      }
      if (mode === "signup") await accountApi.signup(address, password, next, lang);
      else await accountApi.forgot(address, lang);
      trackEvent(mode === "signup" ? "signup" : "password-forgot");
      setStatus("sent");
    } catch (err) {
      const code = err instanceof ApiError ? err.status : 0;
      setStatus(code === 429 ? "limited" : code === 401 ? "wrong" : code === 403 ? "unverified" : "error");
    }
  };

  if (status === "sent") {
    return (
      <div className="login-form">
        <p>{t(mode === "signup" ? "signupSent" : "forgotSent", { email: email.trim() })}</p>
        <p className="muted small">{t(mode === "signup" ? "signupSentHint" : "forgotSentHint")}</p>
        <button className="btn btn-ghost" onClick={() => switchTo("login")}>
          {t("loginBack")}
        </button>
      </div>
    );
  }
  return (
    <div className="login-form">
      {account.google && mode !== "forgot" && (
        <>
          <a className="btn google-btn" href={accountApi.googleHref(next)} onClick={() => trackEvent("login-google")}>
            {GOOGLE_ICON}
            {t("loginGoogle")}
          </a>
          <div className="login-or muted small">{t("loginOr")}</div>
        </>
      )}
      {mode === "forgot" && <p className="muted small">{t("forgotIntro")}</p>}
      <form onSubmit={submit}>
        <label>
          <span>{t("loginEmail")}</span>
          <input
            type="email"
            required
            maxLength={200}
            autoComplete="email"
            autoFocus={autoFocus}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        {mode !== "forgot" && (
          <label>
            <span>{t(mode === "signup" ? "loginNewPassword" : "loginPassword")}</span>
            <input
              type="password"
              required
              minLength={mode === "signup" ? PASSWORD_MIN : undefined}
              maxLength={200}
              autoComplete={mode === "signup" ? "new-password" : "current-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
        )}
        {status === "wrong" && <p className="neg small">{t("loginWrong")}</p>}
        {status === "unverified" && <p className="neg small">{t("loginUnverified")}</p>}
        {status === "error" && <p className="neg small">{t("loginError")}</p>}
        {status === "limited" && <p className="neg small">{t("feedbackLimited")}</p>}
        <button type="submit" className="btn btn-primary" disabled={status === "sending"}>
          {status === "sending" ? t("feedbackSending") : t(mode === "login" ? "loginSubmit" : mode === "signup" ? "signupSubmit" : "forgotSubmit")}
        </button>
      </form>
      <div className="login-links small">
        {mode === "login" ? (
          <>
            <button type="button" className="link-btn" onClick={() => switchTo("signup")}>
              {t("loginToSignup")}
            </button>
            <button type="button" className="link-btn" onClick={() => switchTo("forgot")}>
              {t("loginForgot")}
            </button>
          </>
        ) : (
          <button type="button" className="link-btn" onClick={() => switchTo("login")}>
            {t("loginToLogin")}
          </button>
        )}
      </div>
      <p className="muted small">
        {t("loginPrivacy")} <a href="/privacy">{t("privacyTitle")}</a>
      </p>
    </div>
  );
}

/** A new password, typed twice. */
function NewPasswordForm({ submitLabel, onSubmit }: { submitLabel: string; onSubmit: (password: string) => Promise<void> }) {
  const { t } = useI18n();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [status, setStatus] = useState<"idle" | "saving" | "mismatch" | "error">("idle");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (password !== confirm) return setStatus("mismatch");
    setStatus("saving");
    try {
      await onSubmit(password);
      setPassword("");
      setConfirm("");
      setStatus("idle");
    } catch {
      setStatus("error");
    }
  };
  return (
    <form className="login-form" onSubmit={submit}>
      <label>
        <span>{t("loginNewPassword")}</span>
        <input
          type="password"
          required
          minLength={PASSWORD_MIN}
          maxLength={200}
          autoComplete="new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </label>
      <label>
        <span>{t("passwordConfirm")}</span>
        <input
          type="password"
          required
          maxLength={200}
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />
      </label>
      {status === "mismatch" && <p className="neg small">{t("passwordMismatch")}</p>}
      {status === "error" && <p className="neg small">{t("passwordError")}</p>}
      <button type="submit" className="btn btn-primary" disabled={status === "saving"}>
        {status === "saving" ? t("feedbackSending") : submitLabel}
      </button>
    </form>
  );
}

/** The account page's password: change it, or choose one (signed up with Google). */
function PasswordCard({ hasPassword }: { hasPassword: boolean }) {
  const { t } = useI18n();
  const account = useAccount();
  const [saved, setSaved] = useState(false);
  return (
    <Card title={t("passwordTitle")}>
      {!hasPassword && <p className="muted small">{t("passwordChooseHint")}</p>}
      {saved && <p className="pos small">{t("passwordSaved")}</p>}
      <NewPasswordForm
        submitLabel={t(hasPassword ? "passwordChange" : "passwordChoose")}
        onSubmit={async (password) => {
          setSaved(false);
          await accountApi.setPassword(password);
          trackEvent("password-set");
          await account.refresh();
          setSaved(true);
        }}
      />
    </Card>
  );
}

/** /reset: the "forgotten password" link lands here; the new password signs in (and out
 * everywhere else). */
export function ResetPage() {
  const { t } = useI18n();
  const account = useAccount();
  const token = new URLSearchParams(window.location.search).get("token");
  const [failed, setFailed] = useState(!token);
  useEffect(() => {
    document.title = `${t("resetTitle")} · ${t("appName")}`;
  }, [t]);
  if (!account.enabled) return <NotAvailable />;
  return (
    <div className="account-page narrow">
      <h1>{t("resetTitle")}</h1>
      <Card>
        {failed ? (
          <>
            <p className="neg">{t("loginLinkFailed")}</p>
            <LoginForm next="/" />
          </>
        ) : (
          <NewPasswordForm
            submitLabel={t("resetSubmit")}
            onSubmit={async (password) => {
              try {
                await accountApi.reset(token!, password);
              } catch (err) {
                if (err instanceof ApiError && err.status === 400) return setFailed(true);
                throw err;
              }
              await afterSignIn(account.refresh, "/");
            }}
          />
        )}
      </Card>
    </div>
  );
}

/** The sign-in dialog, opened from anywhere with account.openLogin(); comes back to this page. */
export function LoginDialog() {
  const { t } = useI18n();
  const account = useAccount();
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (account.loginOpen && dialog.current) return showModal(dialog.current);
  }, [account.loginOpen]);
  if (!account.enabled) return null;
  const close = () => dialog.current?.close();
  return (
    <dialog
      ref={dialog}
      className="feedback login"
      aria-labelledby="login-title"
      onClose={account.closeLogin}
      onClick={(e) => e.target === dialog.current && close()}
    >
      {account.loginOpen && (
        <div className="feedback-body">
          <h2 id="login-title">{t("loginTitle")}</h2>
          <p className="muted small">{t("loginIntro")}</p>
          <LoginForm next={window.location.pathname + window.location.search} autoFocus onSignedIn={close} />
          <div className="feedback-actions">
            <button className="btn btn-ghost" onClick={close}>
              {t("close")}
            </button>
          </div>
        </div>
      )}
    </dialog>
  );
}

/** The header's account button: sign in, or the account page (named after "me"). */
export function AccountButton() {
  const { t } = useI18n();
  const account = useAccount();
  if (!account.enabled || !account.ready) return null;
  if (!account.user) {
    return (
      <button className="btn btn-ghost account-btn" onClick={account.openLogin}>
        {t("loginButton")}
      </button>
    );
  }
  return (
    <a className="btn btn-ghost account-btn" href="/account" title={account.user.email}>
      <span aria-hidden>👤</span> {account.me?.name.split(" ")[0] ?? t("accountTitle")}
    </a>
  );
}

/** /login: the verification link lands here and signs in with a POST (mail scanners that open
 * links do not use it up); without a token, the sign-in form. */
export function LoginPage() {
  const { t } = useI18n();
  const account = useAccount();
  const query = new URLSearchParams(window.location.search);
  const token = query.get("token");
  const [failed, setFailed] = useState(query.get("error") === "google");
  const started = useRef(false);

  useEffect(() => {
    document.title = `${t("loginTitle")} · ${t("appName")}`;
    if (!token || started.current) return;
    started.current = true;
    accountApi.verify(token).then(
      ({ next }) => afterSignIn(account.refresh, next),
      () => setFailed(true),
    );
  }, [token, account, t]);

  if (!account.enabled) return <NotAvailable />;
  if (token && !failed) return <Spinner label={t("loginChecking")} />;
  return (
    <div className="account-page narrow">
      <h1>{t("loginTitle")}</h1>
      {failed && <p className="neg">{token ? t("loginLinkFailed") : t("loginGoogleFailed")}</p>}
      {account.user ? (
        <p>
          {t("loginAlready")} <a href="/account">{t("accountTitle")}</a>
        </p>
      ) : (
        <Card>
          <p className="muted small">{t("loginIntro")}</p>
          <LoginForm next="/" autoFocus />
        </Card>
      )}
    </div>
  );
}

function NotAvailable() {
  const { t } = useI18n();
  return (
    <div className="state-screen small">
      <h2>{t("notFound")}</h2>
      <a className="btn" href="/">
        {t("backHome")}
      </a>
    </div>
  );
}

/** /account: "me", the favourites, signing out and deleting the account. */
export function AccountPage() {
  const { t } = useI18n();
  const account = useAccount();
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    document.title = `${t("accountTitle")} · ${t("appName")}`;
  }, [t]);
  if (!account.enabled) return <NotAvailable />;
  const user = account.user;
  if (!user) {
    return (
      <div className="account-page narrow">
        <h1>{t("accountTitle")}</h1>
        <Card>
          <p className="muted small">{t("loginIntro")}</p>
          <LoginForm next="/account" autoFocus />
        </Card>
      </div>
    );
  }
  const me = account.me;
  const remove = async () => {
    if (!window.confirm(t("accountDeleteConfirm"))) return;
    setBusy(true);
    try {
      await account.deleteAccount();
      trackEvent("account-deleted");
      navigate("/");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="account-page narrow">
      <h1>{t("accountTitle")}</h1>
      <Card title={t("meTitle")}>
        <p className="muted small">{t("meIntro")}</p>
        {me ? (
          <div className="me-row">
            <CategoryBadge cls={me.class} />
            <a href={playerHref(me.id)}>
              <strong>{me.name}</strong>
            </a>
            <span className="muted small">{[me.gender === "F" ? t("women") : t("men"), me.age === "A" ? t("age_1") : me.age].filter(Boolean).join(" · ")}</span>
            <button className="btn btn-ghost" onClick={() => account.setMe(null)}>
              {t("meClear")}
            </button>
          </div>
        ) : (
          <p className="hint small">{t("meNone")}</p>
        )}
        <SearchBox
          className="me-search"
          placeholder={me ? t("meChange") : t("mePick")}
          clearOnSelect
          autoFocus={!me}
          onSelect={(p) => {
            trackEvent("me-set");
            void account.setMe(p.id);
          }}
        />
      </Card>
      <FavouritesCard />
      <PasswordCard hasPassword={user.hasPassword} />
      <Card title={t("accountSection")}>
        <p>
          {t("accountEmail")}: <strong>{user.email}</strong>
        </p>
        <div className="account-actions">
          <button className="btn" onClick={() => account.logout().then(() => navigate("/"))}>
            {t("logout")}
          </button>
          <button className="btn btn-ghost neg" disabled={busy} onClick={remove}>
            {t("accountDelete")}
          </button>
        </div>
        <p className="muted small">
          <a href="/privacy">{t("privacyTitle")}</a>
        </p>
      </Card>
    </div>
  );
}

/** The account page's favourites, each with a ★ to remove it. */
function FavouritesCard() {
  const { t } = useI18n();
  const account = useAccount();
  const ids = account.user?.favourites ?? [];
  // names and classes from the "My players" endpoint (loaded again when the list changes)
  const st = useAsync((s) => accountApi.myPlayers(s), [ids.join(",")]);
  return (
    <Card title={t("favouritesTitle", { n: ids.length })}>
      {ids.length === 0 ? (
        <p className="hint small">{t("favouritesNone")}</p>
      ) : !st.data ? (
        <AsyncStatus {...st} />
      ) : (
        <ul className="fav-list">
          {st.data.players.map((p) => (
            <li key={p.id}>
              <FavouriteButton id={p.id} />
              <a href={playerHref(p.id)}>{p.name}</a>
              <ClassChange from={p.official} to={p.today?.class ?? p.official} />
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/** ☆ / ★: follow a player ("My players" on the home page); signed out, it opens the sign-in. */
export function FavouriteButton({ id }: { id: number }) {
  const { t } = useI18n();
  const account = useAccount();
  const [busy, setBusy] = useState(false);
  if (!account.enabled || !account.ready) return null;
  const on = account.isFavourite(id);
  const label = !account.user ? t("favouriteSignIn") : on ? t("favouriteRemove") : t("favouriteAdd");
  const click = async () => {
    if (!account.user) return account.openLogin();
    setBusy(true);
    try {
      await account.toggleFavourite(id);
      trackEvent(on ? "favourite-remove" : "favourite-add");
    } finally {
      setBusy(false);
    }
  };
  return (
    <button className={`fav-btn${on ? " on" : ""}`} onClick={click} disabled={busy} title={label} aria-label={label} aria-pressed={on}>
      {on ? "★" : "☆"}
    </button>
  );
}

/** Home page: the favourites, official class → today's, and their results since the last visit;
 * signed out, an invitation to sign in. */
export function MyPlayersCard() {
  const { t, locale } = useI18n();
  const account = useAccount();
  const ids = account.user?.favourites ?? [];
  const st = useAsync(account.user && ids.length ? (s) => accountApi.myPlayers(s) : null, [account.user?.email, ids.join(",")]);
  if (!account.enabled || !account.ready) return null;
  if (!account.user) {
    return (
      <Card className="my-players my-players-cta">
        <p>
          <strong>{t("myPlayersCtaTitle")}</strong> <span className="muted">{t("myPlayersCta")}</span>
        </p>
        <button className="btn btn-primary" onClick={account.openLogin}>
          {t("loginButton")}
        </button>
      </Card>
    );
  }
  return (
    <Card title={t("myPlayersTitle")} className="my-players">
      {ids.length === 0 ? (
        <p className="hint small">{t("favouritesNone")}</p>
      ) : !st.data ? (
        <AsyncStatus {...st} />
      ) : (
        <>
          <p className="muted small">{t("myPlayersSince", { date: fmtDate(st.data.since, locale) })}</p>
          <div className="my-players-list">
            {st.data.players.map((p) => (
              <div key={p.id} className="my-player">
                <div className="my-player-head">
                  <a href={playerHref(p.id)}>
                    <strong>{p.name}</strong>
                  </a>
                  <ClassChange from={p.official} to={p.today?.class ?? p.official} />
                  <span className="muted small">C {fmt3(p.today?.C)}</span>
                  <span className="muted small my-player-n">
                    {p.nMatches ? t("myPlayersMatches", { n: p.nMatches }) : t("myPlayersNoMatch")}
                  </span>
                </div>
                {p.matches.length > 0 && <MatchTable matches={p.matches} />}
              </div>
            ))}
          </div>
        </>
      )}
    </Card>
  );
}

/** /privacy: what an account keeps, and how to delete it (Swiss data protection law, nFADP). */
export function PrivacyPage() {
  const { t } = useI18n();
  useEffect(() => {
    document.title = `${t("privacyTitle")} · ${t("appName")}`;
  }, [t]);
  return (
    <div className="account-page narrow privacy">
      <h1>{t("privacyTitle")}</h1>
      {(["privacyData", "privacyUse", "privacyCookies", "privacyKeep", "privacyRights", "privacyContact"] as const).map((k) => (
        <p key={k}>{t(k)}</p>
      ))}
    </div>
  );
}
