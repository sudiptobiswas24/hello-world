import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, get, request } from "../api/client";
import { useGet } from "../api/hooks";
import { useAccess } from "../auth/me";
import { dateTime } from "../lib/format";
import { FollowUpsPanel, NotesPanel } from "./Chatter";
import { HistoryPanel } from "./HistoryPanel";

interface AttachmentRow { id: number; name: string; size: number; by: string; at: string; mine: boolean }

const ATTACHMENTS = "/api/core/attachments/";

function sizeOf(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * The files kept with a record (a PO copy, an LR scan): listed, opened,
 * added by whoever may read the record, removed by whoever added one or
 * may remove any.
 */
export function AttachmentsPanel({ model, id }: { model: string; id: number }) {
  const { can } = useAccess();
  const queries = useQueryClient();
  const rows = useGet<AttachmentRow[]>(ATTACHMENTS, { model, id: String(id) });
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  // The history gains a line with every attach and remove, so it is re-read too.
  const refresh = () => {
    void queries.invalidateQueries({ queryKey: ["get", ATTACHMENTS] });
    void queries.invalidateQueries({ queryKey: ["get", "/api/core/history/"] });
  };
  const upload = async (file: File) => {
    const body = new FormData();
    body.append("model", model);
    body.append("id", String(id));
    body.append("file", file);
    setBusy(true);
    setProblem(null);
    try {
      await request(ATTACHMENTS, { method: "POST", body });
      refresh();
    } catch (error) {
      setProblem(error instanceof ApiError ? error.messages.join(" ") : "The file could not be kept.");
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  };
  const remove = async (row: AttachmentRow) => {
    setProblem(null);
    try {
      await request(`${ATTACHMENTS}${row.id}/`, { method: "DELETE" });
      refresh();
    } catch (error) {
      setProblem(error instanceof ApiError ? error.messages.join(" ") : "The file could not be removed.");
    }
  };
  const mayAdd = can("core.add_attachment");
  if (!rows.data?.length && !mayAdd) return null;
  return (
    <section className="related-list chatter" aria-label="Attachments">
      <h2>Attachments</h2>
      {rows.data?.length ? (
        <table className="data">
          <tbody>
            {rows.data.map((row) => (
              <tr key={row.id}>
                <td><a href={`${ATTACHMENTS}${row.id}/download/`}>{row.name}</a></td>
                <td className="muted">{sizeOf(row.size)}</td>
                <td className="muted">{row.by} · {dateTime(row.at)}</td>
                <td>
                  {(row.mine || can("core.delete_attachment")) && (
                    <button type="button" className="icon-btn" aria-label={`Remove ${row.name}`} onClick={() => void remove(row)}>×</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <p className="muted">None yet.</p>}
      {mayAdd && (
        <label className="btn">
          {busy ? "Keeping…" : "Attach a file"}
          <input ref={input} type="file" hidden disabled={busy} accept=".pdf,.png,.jpg,.jpeg,.xlsx,.xls,.csv,.docx,.txt"
            onChange={(event) => { const file = event.target.files?.[0]; if (file) void upload(file); }} />
        </label>
      )}
      {problem && <p className="form-error" role="alert">{problem}</p>}
    </section>
  );
}

/**
 * What a record carries with it and what happened to it: what is planned
 * on it, what people said, its files and its history.
 */
export function Trail({ model, id }: { model: string; id: number }) {
  return (
    <>
      <FollowUpsPanel model={model} id={id} />
      <NotesPanel model={model} id={id} />
      <AttachmentsPanel model={model} id={id} />
      <HistoryPanel model={model} id={id} />
    </>
  );
}

/**
 * The record kind a screen's address serves, from the server's own routes:
 * a record screen knows where it reads, not what it is.
 */
export function useKindOf(endpoint: string): string | undefined {
  const kinds = useQuery({
    queryKey: ["reference", "/api/core/endpoints/"],
    queryFn: ({ signal }) => get<Record<string, string>>("/api/core/endpoints/", undefined, signal),
    staleTime: Infinity,
  });
  return kinds.data?.[endpoint];
}
