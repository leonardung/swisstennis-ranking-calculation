/** A table (a header row, then rows of texts or numbers) saved as an Excel, CSV or PDF file.
 * The Excel and PDF writers are loaded on the first export only. */

export type ExportFormat = "xlsx" | "csv" | "pdf";
export type ExportValue = string | number | null | undefined;

export interface ExportTable {
  /** the file name without its extension */
  fileName: string;
  /** the PDF's title and subtitle */
  title: string;
  subtitle: string;
  header: string[];
  rows: ExportValue[][];
}

function save(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** RFC 4180, with a BOM so that Excel reads it as UTF-8. */
function csv({ header, rows }: ExportTable): Blob {
  const cell = (v: ExportValue) => {
    const s = v == null ? "" : String(v);
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const text = [header, ...rows].map((r) => r.map(cell).join(",")).join("\r\n");
  return new Blob(["﻿", text, "\r\n"], { type: "text/csv;charset=utf-8" });
}

async function xlsx(t: ExportTable): Promise<Blob> {
  const { default: writeXlsxFile } = await import("write-excel-file/browser");
  const width = (i: number) => Math.min(40, Math.max(6, ...[t.header[i], ...t.rows.map((r) => r[i])].map((v) => String(v ?? "").length + 2)));
  return writeXlsxFile(
    [
      t.header.map((h) => ({ value: h, fontWeight: "bold" as const })),
      ...t.rows.map((r) => r.map((v) => (v == null || v === "" ? null : v))),
    ],
    { sheet: t.title.replace(/[:\\/?*[\]]/g, " ").slice(0, 31) || "Sheet1", stickyRowsCount: 1, columns: t.header.map((_, i) => ({ width: width(i) })) },
  ).toBlob();
}

async function pdf(t: ExportTable): Promise<Blob> {
  // CommonJS bundles: the module is the default export; the fonts (Roboto: every Latin letter)
  const [{ default: pdfMake }, { default: vfs }] = await Promise.all([import("pdfmake/build/pdfmake"), import("pdfmake/build/vfs_fonts")]);
  pdfMake.addVirtualFileSystem(vfs);
  const numeric = t.header.map((_, i) => t.rows.some((r) => typeof r[i] === "number"));
  const longest = t.header.map((_, i) => Math.max(0, ...t.rows.map((r) => (typeof r[i] === "string" ? (r[i] as string).length : 0))));
  const widest = longest.indexOf(Math.max(...longest));
  return pdfMake
    .createPdf({
      pageSize: "A4",
      pageOrientation: t.header.length > 6 ? "landscape" : "portrait",
      pageMargins: [32, 40, 32, 40],
      info: { title: t.title },
      defaultStyle: { fontSize: 9 },
      content: [
        { text: t.title, fontSize: 15, bold: true },
        { text: t.subtitle, color: "#666", margin: [0, 2, 0, 10] },
        {
          table: {
            headerRows: 1,
            // the longest texts (the names) take the rest of the page's width
            widths: t.header.map((_, i) => (i === widest ? "*" : "auto")),
            body: [
              t.header.map((h, i) => ({ text: h, bold: true, alignment: numeric[i] ? ("right" as const) : ("left" as const) })),
              ...t.rows.map((r) => r.map((v, i) => ({ text: v == null ? "" : String(v), alignment: numeric[i] ? ("right" as const) : ("left" as const) }))),
            ],
          },
          layout: "lightHorizontalLines",
        },
      ],
      footer: (page, pages) => ({ text: `${page} / ${pages}`, alignment: "right", margin: [0, 10, 32, 0], fontSize: 8, color: "#666" }),
    })
    .getBlob();
}

export async function exportTable(format: ExportFormat, t: ExportTable): Promise<void> {
  const blob = format === "csv" ? csv(t) : format === "xlsx" ? await xlsx(t) : await pdf(t);
  save(blob, `${t.fileName}.${format}`);
}
