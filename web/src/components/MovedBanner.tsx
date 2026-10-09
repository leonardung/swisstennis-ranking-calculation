import { useEffect, useState } from "react";
import { appConfig } from "../config";
import { useI18n } from "../i18n";

const HIDDEN_KEY = "movedBannerHidden";
// a hidden banner comes back after this long, so the new address is not forgotten
const HIDE_MS = 86_400_000; // a day

function hiddenNow(): boolean {
  try {
    return Date.now() - Number(localStorage.getItem(HIDDEN_KEY) ?? 0) < HIDE_MS;
  } catch {
    return false; // storage unavailable
  }
}

/** On the old address (MOVED_FROM on the server): the site has moved, and the button opens the same
 * page at the new address. Nobody is redirected without clicking. */
export default function MovedBanner() {
  const { t } = useI18n();
  const [movedTo, setMovedTo] = useState<string | null>(null);
  const [hidden, setHidden] = useState(hiddenNow);
  useEffect(() => {
    appConfig.then((c) => setMovedTo(c.moved_to)).catch(() => {});
  }, []);
  if (!movedTo || hidden) return null;
  const hide = () => {
    setHidden(true);
    try {
      localStorage.setItem(HIDDEN_KEY, String(Date.now()));
    } catch {
      /* storage unavailable: hidden until the next page load */
    }
  };
  const { pathname, search } = window.location;
  return (
    <div className="moved-banner" role="status">
      <div className="container moved-inner">
        <p>
          <strong>{t("movedTitle")}</strong>
          {t("movedText")}
          <span className="moved-note">{t("movedBlocked")}</span>
        </p>
        <a className="btn" href={movedTo.replace(/\/$/, "") + pathname + search}>
          {t("movedLink")}
        </a>
        <button className="btn btn-ghost" onClick={hide} aria-label={t("movedHide")} title={t("movedHide")}>
          ✕
        </button>
      </div>
    </div>
  );
}
