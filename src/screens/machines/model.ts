/**
 * View-model helpers for the Machines & planning screen — straight ports of the
 * design's "machines" and "planning" sections of renderVals().
 */
import { fmt, num, plan } from '../../engine';
import type { AppState, CalcResult, MachineType, NumInput, Sku } from '../../engine';

/** Stage options for a machine row, in design order. */
export const MACHINE_TYPE_OPTIONS: { value: MachineType; label: string }[] = [
  { value: 'tape', label: 'Tape plant' },
  { value: 'loom', label: 'Circular loom' },
  { value: 'lamination', label: 'Lamination' },
  { value: 'printing', label: 'Printing' },
  { value: 'cutting', label: 'Cutting + stitching' },
];

/** Editable parameters per machine type: [key in `machine.p`, label]. */
export const PARAM_DEFS: Record<MachineType, [string, string][]> = {
  tape: [['speed', 'Speed m/min'], ['tapes', 'Tapes']],
  loom: [['rpm', 'Cam RPM'], ['shuttles', 'Shuttles']],
  lamination: [['speed', 'Speed m/min']],
  printing: [['bph', 'Bags/hr']],
  cutting: [['bph', 'Bags/hr']],
};

/** Parameters a machine gets when its stage is changed. */
export const TYPE_DEFAULT_PARAMS: Record<MachineType, Record<string, NumInput>> = {
  tape: { speed: 300, tapes: 80 },
  loom: { rpm: 90, shuttles: 6 },
  lamination: { speed: 100 },
  printing: { bph: 3000 },
  cutting: { bph: 1800 },
};

export function isMachineType(t: string): t is MachineType {
  return Object.prototype.hasOwnProperty.call(PARAM_DEFS, t);
}

/** Selected spec id if it still exists, otherwise the first spec (or null when there are none). */
export function resolveSel(skus: Sku[], sel: string | undefined): string | null {
  const firstId = skus[0] ? skus[0].id : null;
  return sel && skus.some((k) => k.id === sel) ? sel : firstId;
}

export interface OrderOption {
  id: string;
  label: string;
}

/** Order picker entries: "JOB-101 — Name — 50,000 bags". */
export function orderOptions(skus: Sku[]): OrderOption[] {
  return skus.map((s) => ({ id: s.id, label: (s.jobId ? s.jobId + ' — ' : '') + s.name + ' — ' + fmt(num(s.qty), 0) + ' bags' }));
}

export type PlanRowTone = 'bottleneck' | 'noMachine' | null;

export interface PlanRowView {
  stage: string;
  req: string;
  cap: string;
  hours: string;
  days: string;
  /** "· bottleneck", "· no machine" or "" */
  flag: string;
  tone: PlanRowTone;
}

export interface PlanView {
  planSelId: string | null;
  rows: PlanRowView[];
  totalS: string;
  bottleneckS: string;
  qtyS: string;
  kgS: string;
  mS: string;
  shiftNote: string;
}

export function planView(state: AppState, results: Record<string, CalcResult>): PlanView {
  const planSelId = resolveSel(state.skus, state.planSel);
  const view: PlanView = {
    planSelId,
    rows: [],
    totalS: '—',
    bottleneckS: '',
    qtyS: '—',
    kgS: '—',
    mS: '—',
    shiftNote: num(state.glob.workHours) + ' working h/day (edit in Rates)',
  };
  if (!planSelId) return view;
  const s = state.skus.find((k) => k.id === planSelId);
  const r = results[planSelId];
  if (!s || !r) return view;
  const pl = plan(state, s, r);
  view.qtyS = fmt(r.qty, 0) + ' bags';
  view.kgS = fmt(r.allKgOrder, 0) + ' kg';
  view.mS = fmt(r.fabricM, 0) + ' m';
  view.totalS = fmt(pl.maxDays, 1) + ' days';
  view.bottleneckS = pl.bottleneck
    ? 'Bottleneck: ' + pl.bottleneck + '. Stages run in parallel once fed; the slowest stage sets the delivery date. Add capacity there first.'
    : '';
  view.rows = pl.rows.map((p) => {
    const isBottleneck = p.stage === pl.bottleneck;
    const noMachine = p.capN <= 0;
    return {
      stage: p.stage,
      req: p.req,
      cap: p.cap,
      hours: Number.isFinite(p.hours) ? fmt(p.hours, 0) : '—',
      days: Number.isFinite(p.days) ? fmt(p.days, 1) : '—',
      flag: isBottleneck ? '· bottleneck' : noMachine ? '· no machine' : '',
      tone: isBottleneck ? 'bottleneck' : noMachine ? 'noMachine' : null,
    };
  });
  return view;
}
