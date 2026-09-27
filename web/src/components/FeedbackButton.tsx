import { useRef, useState, type FormEvent } from "react";
import { trackEvent } from "../analytics";
import { useI18n } from "../i18n";

type Status = "idle" | "sending" | "sent" | "error" | "limited";

/** "Give feedback" button and its dialog: a message, optional name and email, and the page. */
export default function FeedbackButton() {
  const { t } = useI18n();
  const dialog = useRef<HTMLDialogElement>(null);
  const [status, setStatus] = useState<Status>("idle");

  const open = () => {
    setStatus("idle");
    dialog.current?.showModal();
  };
  const close = () => dialog.current?.close();

  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const text = (k: string) => String(form.get(k) ?? "").trim() || null;
    const body = {
      message: text("message") ?? "",
      name: text("name"),
      email: text("email"),
      page: window.location.pathname,
      website: text("website"),
    };
    setStatus("sending");
    try {
      const res = await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (res.status === 429) return setStatus("limited");
      if (!res.ok) return setStatus("error");
    } catch {
      return setStatus("error");
    }
    // the message itself shows in the Umami dashboard (event data keeps 500 characters)
    const data: Record<string, string> = { message: body.message.slice(0, 500), page: body.page };
    if (body.name) data.name = body.name;
    if (body.email) data.email = body.email;
    trackEvent("feedback", data);
    e.currentTarget?.reset();
    setStatus("sent");
  };

  return (
    <>
      <button className="btn btn-ghost feedback-btn" onClick={open}>
        {t("feedbackButton")}
      </button>
      <dialog ref={dialog} className="feedback" aria-labelledby="feedback-title" onClick={(e) => e.target === dialog.current && close()}>
        <div className="feedback-body">
          <h2 id="feedback-title">{t("feedbackTitle")}</h2>
          {status === "sent" ? (
            <>
              <p>{t("feedbackThanks")}</p>
              <div className="feedback-actions">
                <button className="btn" onClick={close} autoFocus>
                  {t("feedbackClose")}
                </button>
              </div>
            </>
          ) : (
            <form onSubmit={submit}>
              <p className="muted small">{t("feedbackIntro")}</p>
              <label>
                <span>{t("feedbackMessage")}</span>
                <textarea name="message" required maxLength={4000} rows={6} autoFocus />
              </label>
              <div className="feedback-row">
                <label>
                  <span>
                    {t("feedbackName")} <small className="muted">({t("feedbackOptional")})</small>
                  </span>
                  <input name="name" maxLength={100} autoComplete="name" />
                </label>
                <label>
                  <span>
                    {t("feedbackEmail")} <small className="muted">({t("feedbackOptional")})</small>
                  </span>
                  <input name="email" type="email" maxLength={200} autoComplete="email" placeholder={t("feedbackEmailHint")} />
                </label>
              </div>
              {/* trap for bots, hidden from people and screen readers */}
              <input name="website" className="feedback-trap" tabIndex={-1} autoComplete="off" aria-hidden />
              {status === "error" && <p className="neg small">{t("feedbackError")}</p>}
              {status === "limited" && <p className="neg small">{t("feedbackLimited")}</p>}
              <div className="feedback-actions">
                <button type="button" className="btn btn-ghost" onClick={close}>
                  {t("feedbackCancel")}
                </button>
                <button type="submit" className="btn btn-primary" disabled={status === "sending"}>
                  {status === "sending" ? t("feedbackSending") : t("feedbackSend")}
                </button>
              </div>
            </form>
          )}
        </div>
      </dialog>
    </>
  );
}
