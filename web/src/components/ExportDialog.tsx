import { useEffect, useRef, useState } from "react";
import { showModal } from "../hooks";
import { useI18n } from "../i18n";
import type { ExportFormat } from "../export";
import { Seg } from "./common";

export interface ExportColumn {
  key: string;
  label: string;
  /** ticked at first (default true) */
  on?: boolean;
}

const FORMATS: [ExportFormat, string][] = [
  ["xlsx", "Excel"],
  ["csv", "CSV"],
  ["pdf", "PDF"],
];

/** Popup (native modal dialog): the file format and the columns to export, then the download.
 * `onExport` gets the chosen columns' keys in their order; the popup closes once it resolves. */
export default function ExportDialog({
  summary,
  columns,
  onExport,
  onClose,
}: {
  summary: string;
  columns: ExportColumn[];
  onExport: (format: ExportFormat, keys: string[]) => Promise<void>;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const dialog = useRef<HTMLDialogElement>(null);
  const [format, setFormat] = useState<ExportFormat>("xlsx");
  const [picked, setPicked] = useState(() => new Set(columns.filter((c) => c.on !== false).map((c) => c.key)));
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => showModal(dialog.current!), []);
  const close = () => dialog.current?.close();
  const all = picked.size === columns.length;
  const toggle = (k: string) =>
    setPicked((s) => {
      const n = new Set(s);
      if (!n.delete(k)) n.add(k);
      return n;
    });
  const download = async () => {
    setBusy(true);
    setFailed(false);
    try {
      await onExport(
        format,
        columns.filter((c) => picked.has(c.key)).map((c) => c.key),
      );
      close();
    } catch (e) {
      console.error(e);
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <dialog
      ref={dialog}
      className="match-dialog export-dialog"
      aria-labelledby="export-dialog-title"
      onClose={onClose}
      onClick={(e) => e.target === dialog.current && close()}
    >
      <div className="match-dialog-head">
        <div>
          <h2 id="export-dialog-title">{t("exportTitle")}</h2>
          <small className="muted">{summary}</small>
        </div>
        <button className="btn btn-ghost" onClick={close} aria-label={t("close")}>
          ✕
        </button>
      </div>
      <div className="match-dialog-body export-body">
        <fieldset>
          <legend>{t("exportFormat")}</legend>
          <Seg value={format} options={FORMATS} onChange={setFormat} />
        </fieldset>
        <fieldset>
          <legend>
            {t("exportColumns")}
            <button type="button" className="link-btn" onClick={() => setPicked(new Set(all ? [] : columns.map((c) => c.key)))}>
              {all ? t("exportSelectNone") : t("exportSelectAll")}
            </button>
          </legend>
          <div className="export-cols">
            {columns.map((c) => (
              <label key={c.key}>
                <input type="checkbox" checked={picked.has(c.key)} onChange={() => toggle(c.key)} />
                {c.label}
              </label>
            ))}
          </div>
        </fieldset>
        {failed && <p className="hint export-error">{t("exportFailed")}</p>}
        <div className="export-actions">
          <button className="btn btn-primary" disabled={busy || picked.size === 0} onClick={download}>
            {busy ? "…" : `⤓ ${t("exportDownload")}`}
          </button>
        </div>
      </div>
    </dialog>
  );
}
