import { useSearchParams } from "react-router";

import { useGet } from "../../api/hooks";
import { ErrorPanel } from "../../shell/ErrorPanel";

interface Plan { letter: string; sample: number; accept: number; reject: number; whole_lot: boolean; [key: string]: unknown }

/** How many sacks to pull from a batch, and how many may fail, by ISO 2859-1. */
export default function Sampling() {
  const [params, setParams] = useSearchParams();
  const lotSize = params.get("lot_size") ?? "";
  const level = params.get("level") ?? "II";
  const aql = params.get("aql") ?? "2.5";
  const ready = /^\d+$/.test(lotSize) && Number(lotSize) > 0;
  const plan = useGet<Plan>("/api/quality/sampling/", { lot_size: lotSize, level, aql }, ready);
  const set = (key: string, value: string) => setParams((current) => {
    const next = new URLSearchParams(current);
    next.set(key, value);
    return next;
  }, { replace: true });
  return (
    <section className="report">
      <header className="list-head">
        <h1>Sampling</h1>
        <label className="inline">Batch of <input inputMode="numeric" value={lotSize} onChange={(e) => set("lot_size", e.target.value.replace(/\D/g, ""))} /></label>
        <label className="inline">Level <select value={level} onChange={(e) => set("level", e.target.value)}>
          {["S-1", "S-2", "S-3", "S-4", "I", "II", "III"].map((v) => <option key={v} value={v}>{v}</option>)}
        </select></label>
        <label className="inline">AQL <select value={aql} onChange={(e) => set("aql", e.target.value)}>
          {["0.65", "1.0", "1.5", "2.5", "4.0", "6.5"].map((v) => <option key={v} value={v}>{v}</option>)}
        </select></label>
      </header>
      {!ready ? <div className="empty"><p>Say how many are in the batch.</p></div>
        : plan.isError ? <ErrorPanel error={plan.error} retry={() => void plan.refetch()} />
        : plan.data ? (
          <dl className="totals">
            <div className="strong"><dt>Pull</dt><dd>{plan.data.sample}{plan.data.whole_lot ? " (all of them)" : ""}</dd></div>
            <div><dt>Code letter</dt><dd>{plan.data.letter}</dd></div>
            <div><dt>Accept with up to</dt><dd>{plan.data.accept} failing</dd></div>
            <div><dt>Reject at</dt><dd>{plan.data.reject} failing</dd></div>
          </dl>
        ) : <div className="loading">Working it out…</div>}
    </section>
  );
}
