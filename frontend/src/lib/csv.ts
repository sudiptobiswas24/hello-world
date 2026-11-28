/**
 * A list or report as a CSV file, from the columns the screen shows and
 * the values as the server sent them: money and quantities are exact
 * strings already, dates are ISO. Opened in a spreadsheet as-is; the
 * byte-order mark keeps names in Hindi or Bengali readable there.
 */

interface CsvColumn {
  key: string;
  label: string;
}

const cell = (value: unknown): string => {
  if (value === null || value === undefined) return "";
  if (typeof value === "boolean") return value ? "yes" : "no";
  const text = typeof value === "object" ? JSON.stringify(value) : String(value);
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
};

/** The screen's columns as a header, then one line a row; columns the screen keeps for its own buttons (keys starting with "_") are left out. */
export function csvText(columns: CsvColumn[], rows: Record<string, unknown>[]): string {
  const shown = columns.filter((column) => !column.key.startsWith("_"));
  const lines = [shown.map((column) => cell(column.label || column.key)).join(",")];
  for (const row of rows) lines.push(shown.map((column) => cell(row[column.key])).join(","));
  return `﻿${lines.join("\r\n")}\r\n`;
}

export function csvFileName(title: string): string {
  return `${title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "export"}.csv`;
}

/** Hands the text to the browser as a file to save. */
export function downloadCsv(title: string, text: string): void {
  const blob = new Blob([text], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = csvFileName(title);
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
