import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";

import { ApiError, send } from "../../api/client";
import { useAct, useRecord } from "../../api/hooks";
import { quantity } from "../../lib/format";
import { drop, enqueue, kept, scanKey, waiting, type QueuedScan } from "../../lib/scanQueue";
import { ScanBox } from "../../views/ScanBox";

/**
 * The scan screens (L9): receive, pick, issue, transfer, count. Each reads
 * every code through ScanBox and sends it to the document's own scan
 * endpoint, which reads it through the one resolver; none parses a code.
 * A scan never posts: the document's own page does, with its own checks.
 *
 * Receive, pick, issue and transfer need the network and say so when it is
 * gone. A count keeps its scans on the device and sends them when it
 * returns, each with a key made here, which the server counts once.
 */

interface Line {
  id: number;
  lot?: number | null;
  lot_code?: string;
  description?: string;
  item_label?: string;
  quantity?: string;
  quantity_received?: string;
  quantity_shipped?: string;
  counted_quantity?: string;
  uom_code?: string;
  returns_line?: number | null;
}
interface Doc {
  id: number;
  number?: string;
  posted?: boolean;
  status?: string;
  lines?: Line[];
}

function useOnline(): boolean {
  const [online, setOnline] = useState(typeof navigator === "undefined" ? true : navigator.onLine);
  useEffect(() => {
    const up = () => setOnline(true);
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    return () => {
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
    };
  }, []);
  return online;
}

function problemOf(error: unknown, code: string): string {
  if (error instanceof ApiError) return error.messages.join(" ");
  return `${code} could not be sent.`;
}

const OFFLINE = "No network: this screen needs it to check each scan. Scan again when the signal is back.";

function Shell({ title, doc, back, children }: { title: string; doc: Doc | undefined; back: string; children: ReactNode }) {
  const number = doc?.number ? doc.number : doc ? `draft #${doc.id}` : "";
  return (
    <article className="doc scan-screen">
      <header className="list-head">
        <h1>{title} {number}</h1>
        <Link className="btn" to={back}>Open the document</Link>
      </header>
      {children}
    </article>
  );
}

function Lines({ rows, amount, action }: {
  rows: Line[]; amount: (line: Line) => string | undefined;
  action?: { label: string; run: (line: Line) => void; when?: (line: Line) => boolean };
}) {
  if (!rows.length) return <p className="muted">Nothing scanned yet.</p>;
  return (
    <ul className="scan-lines" aria-label="Lines">
      {rows.map((line) => (
        <li key={line.id}>
          <span>{line.item_label ?? line.description ?? ""}</span>
          <span>{line.lot_code ? `batch ${line.lot_code}` : "no batch"}</span>
          <b>{quantity(amount(line) ?? "")}{line.uom_code ? ` ${line.uom_code}` : ""}</b>
          {action && (action.when ?? (() => true))(line) && (
            <button type="button" className="btn" onClick={() => action.run(line)}>{action.label}</button>
          )}
        </li>
      ))}
    </ul>
  );
}

/** Receive, pick and issue: one box, the document's scan endpoint, each line's way back. */
function OnlineScan({ title, endpoint, back, withQuantity, amount, undo, scanLabel, hint }: {
  title: string; endpoint: string; back: (id: string) => string; withQuantity: boolean;
  amount: (line: Line) => string | undefined; scanLabel: string; hint: string;
  undo: { label: string; when?: (line: Line) => boolean; request: (doc: Doc, line: Line) => { method: "POST" | "DELETE"; path: string; body?: unknown } };
}) {
  const { id } = useParams();
  const record = useRecord<Doc>(endpoint, id);
  const act = useAct<{ line: Line | null }>();
  const online = useOnline();
  const [amountTyped, setAmount] = useState("1");
  const [problem, setProblem] = useState<string | null>(null);
  const doc = record.data;
  const closed = Boolean(doc && (doc.posted || (doc.status && doc.status !== "draft")));

  const scanned = async (code: string) => {
    setProblem(null);
    if (!navigator.onLine) {
      setProblem(OFFLINE);
      return;
    }
    const body: Record<string, unknown> = { code };
    if (withQuantity) body.quantity = amountTyped;
    const outcome = await act.run("POST", `${endpoint}${id}/scan/`, body, { done: `${code} scanned` });
    if (!outcome.ok) setProblem(outcome.error.kind === "offline" ? OFFLINE : problemOf(outcome.error, code));
  };
  const undoLine = async (line: Line) => {
    if (!doc) return;
    const asked = undo.request(doc, line);
    const outcome = await act.run(asked.method, asked.path, asked.body, { done: "Taken off" });
    if (!outcome.ok) setProblem(problemOf(outcome.error, ""));
  };

  return (
    <Shell title={title} doc={doc} back={back(id ?? "")}>
      <section className="sheet">
        {!online && <p className="form-error" role="alert">{OFFLINE}</p>}
        {closed && <p className="form-error" role="alert">This document has posted; a scan no longer changes it.</p>}
        <p className="muted">{hint}</p>
        {withQuantity && (
          <label className="field">Quantity{" "}
            <input aria-label="Quantity" inputMode="decimal" value={amountTyped} onChange={(event) => setAmount(event.target.value)} />
          </label>
        )}
        <ScanBox label={scanLabel} onScan={scanned} busy={act.pending || !online || closed} />
        {problem && <p className="form-error" role="alert">{problem}</p>}
        <Lines rows={doc?.lines ?? []} amount={amount}
          action={closed ? undefined : { label: undo.label, run: (line) => void undoLine(line), when: undo.when }} />
      </section>
    </Shell>
  );
}

export function ScanReceive() {
  return (
    <OnlineScan title="Receive" endpoint="/api/purchasing/goods-receipts/" back={(id) => `/purchasing/goods-in/${id}`}
      withQuantity scanLabel="Item or batch" hint="Scan each item or batch as it comes off the lorry, with how many."
      amount={(line) => line.quantity_received}
      undo={{ label: "Remove", request: (_doc, line) => ({ method: "DELETE", path: `/api/purchasing/goods-receipt-lines/${line.id}/` }) }} />
  );
}

export function ScanPick() {
  return (
    <OnlineScan title="Pick" endpoint="/api/sales/deliveries/" back={(id) => `/sales/deliveries/${id}`}
      withQuantity={false} scanLabel="Batch" hint="Scan the batch label on each bag you take; it is named on its line."
      amount={(line) => line.quantity_shipped}
      undo={{ label: "Take off", when: (line) => Boolean(line.lot),
        request: (doc, line) => ({ method: "POST", path: `/api/sales/deliveries/${doc.id}/unpick/`, body: { line: line.id } }) }} />
  );
}

export function ScanIssue() {
  return (
    <OnlineScan title="Issue" endpoint="/api/manufacturing/material-issues/" back={(id) => `/production/issues/${id}`}
      withQuantity={false} scanLabel="Batch" hint="Scan the batch label of what goes to the run; it is named on its line."
      amount={(line) => line.quantity}
      undo={{ label: "Take off", when: (line) => Boolean(line.lot),
        request: (doc, line) => ({ method: "POST", path: `/api/manufacturing/material-issues/${doc.id}/unpick/`, body: { line: line.id } }) }} />
  );
}

interface Bin { id: number; code: string }

export function ScanTransfer() {
  const { id } = useParams();
  const endpoint = "/api/inventory/stock-transfers/";
  const record = useRecord<Doc>(endpoint, id);
  const act = useAct<{ bin?: Bin; line?: Line; lines?: Line[] }>();
  const online = useOnline();
  const [fromBin, setFromBin] = useState<Bin | null>(null);
  const [amountTyped, setAmount] = useState("1");
  const [problem, setProblem] = useState<string | null>(null);
  const doc = record.data;
  const closed = Boolean(doc && doc.status !== "draft");

  const stage = (which: "from" | "goods" | "to") => async (code: string) => {
    setProblem(null);
    if (!navigator.onLine) {
      setProblem(OFFLINE);
      return;
    }
    const body: Record<string, unknown> = { code, stage: which };
    if (which === "goods") {
      body.quantity = amountTyped;
      if (fromBin) body.from_bin = fromBin.code;
    }
    const outcome = await act.run("POST", `${endpoint}${id}/scan/`, body, {
      done: which === "from" ? `From ${code}` : which === "to" ? `To ${code}` : `${code} scanned` });
    if (!outcome.ok) {
      setProblem(outcome.error.kind === "offline" ? OFFLINE : problemOf(outcome.error, code));
      return;
    }
    if (which === "from" && outcome.data.bin) setFromBin(outcome.data.bin);
  };
  const remove = async (line: Line) => {
    const outcome = await act.run("DELETE", `/api/inventory/stock-transfer-lines/${line.id}/`, undefined, { done: "Removed" });
    if (!outcome.ok) setProblem(problemOf(outcome.error, ""));
  };

  return (
    <Shell title="Transfer" doc={doc} back={`/stores/transfers/${id ?? ""}`}>
      <section className="sheet">
        {!online && <p className="form-error" role="alert">{OFFLINE}</p>}
        {closed && <p className="form-error" role="alert">This transfer has moved stock; a scan no longer changes it.</p>}
        <p className="muted">Scan the bin the goods leave, then each batch or item with how many, then the bin they go to.</p>
        <ScanBox label="From bin" onScan={stage("from")} busy={act.pending || !online || closed} />
        <p className="muted" aria-live="polite">{fromBin ? `Taking from ${fromBin.code}` : "No bin scanned: from anywhere in the warehouse."}</p>
        <label className="field">Quantity{" "}
          <input aria-label="Quantity" inputMode="decimal" value={amountTyped} onChange={(event) => setAmount(event.target.value)} />
        </label>
        <ScanBox label="Item or batch" onScan={stage("goods")} busy={act.pending || !online || closed} />
        <ScanBox label="To bin" onScan={stage("to")} busy={act.pending || !online || closed} />
        {problem && <p className="form-error" role="alert">{problem}</p>}
        <Lines rows={doc?.lines ?? []} amount={(line) => line.quantity}
          action={closed ? undefined : { label: "Remove", run: (line) => void remove(line) }} />
      </section>
    </Shell>
  );
}

export function ScanCount() {
  const { id } = useParams();
  const endpoint = "/api/inventory/stock-counts/";
  const path = `${endpoint}${id ?? ""}/scan/`;
  const record = useRecord<Doc>(endpoint, id);
  const queryClient = useQueryClient();
  const online = useOnline();
  const [bin, setBin] = useState("");
  const [amountTyped, setAmount] = useState("1");
  const [queued, setQueued] = useState<QueuedScan[]>([]);
  const [problems, setProblems] = useState<string[]>([]);
  const [lasting, setLasting] = useState(true);
  const sending = useRef(false);
  const act = useAct();
  const doc = record.data;
  const closed = Boolean(doc?.posted);
  const remove = async (line: Line) => {
    const outcome = await act.run("DELETE", `/api/inventory/stock-count-lines/${line.id}/`, undefined, { done: "Removed" });
    if (!outcome.ok) setProblems((said) => [...said, problemOf(outcome.error, "")]);
  };

  const reread = useCallback(async () => setQueued(await waiting(path)), [path]);

  /** Send what waits, oldest first; stop at the first that finds no network. */
  const flush = useCallback(async () => {
    if (sending.current) return;
    sending.current = true;
    try {
      for (const scan of await waiting(path)) {
        try {
          await send("POST", scan.path, scan.body);
          await drop(scan.key);
        } catch (error) {
          if (error instanceof ApiError && (error.kind === "offline" || error.kind === "server" || error.kind === "signed-out")) break;
          // Refused (a bin of another store, a posted count): said, and not sent again.
          setProblems((said) => [...said, `${scan.label}: ${problemOf(error, scan.label)}`]);
          await drop(scan.key);
        }
      }
    } finally {
      sending.current = false;
      await reread();
      await queryClient.invalidateQueries({ queryKey: ["record", endpoint, String(id)] });
    }
  }, [path, reread, queryClient, id]);

  useEffect(() => {
    void kept().then(setLasting);
    void reread().then(() => flush());
    const back = () => void flush();
    window.addEventListener("online", back);
    return () => window.removeEventListener("online", back);
  }, [reread, flush]);

  const scanned = async (code: string) => {
    const scan: QueuedScan = {
      key: scanKey(), path, at: Date.now(),
      body: { code, quantity: amountTyped, ...(bin ? { bin } : {}) },
      label: `${code} × ${amountTyped}${bin ? ` in ${bin}` : ""}`,
    };
    await enqueue(scan);
    await reread();
    if (navigator.onLine) await flush();
  };

  return (
    <Shell title="Count" doc={doc} back={`/stores/counts/${id ?? ""}`}>
      <section className="sheet">
        {!online && (
          <p className="form-error" role="status">
            No network: scans are kept on this device and sent when it returns.
            {!lasting && " This window cannot keep them if it is closed: keep it open."}
          </p>
        )}
        {closed && <p className="form-error" role="alert">This count has posted; a scan no longer changes it.</p>}
        <p className="muted">Scan the bin, then each item or batch in it with how many you found.</p>
        <ScanBox label="Bin" onScan={(code) => setBin(code)} busy={closed} />
        <p className="muted" aria-live="polite">{bin ? `Counting in ${bin}` : "No bin scanned: the whole warehouse."}</p>
        <label className="field">Quantity{" "}
          <input aria-label="Quantity" inputMode="decimal" value={amountTyped} onChange={(event) => setAmount(event.target.value)} />
        </label>
        <ScanBox label="Item or batch" onScan={scanned} busy={closed} />
        {queued.length > 0 && (
          <section aria-label="Waiting to be sent">
            <h2>Waiting to be sent ({queued.length})</h2>
            <ul>{queued.map((scan) => <li key={scan.key}>{scan.label}</li>)}</ul>
          </section>
        )}
        {problems.map((said) => <p key={said} className="form-error" role="alert">{said}</p>)}
        <Lines rows={doc?.lines ?? []} amount={(line) => line.counted_quantity}
          action={closed || !online ? undefined : { label: "Remove", run: (line) => void remove(line) }} />
      </section>
    </Shell>
  );
}
