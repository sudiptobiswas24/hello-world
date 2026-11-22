/**
 * View-model helpers for the lower half of the cost sheet (derived strip,
 * quantities, planning panels, costing, plan strip).
 *
 * Pure string formatting only: every number comes from the engine (`calc`,
 * `loomPlan`, `tapePlan`, …). Formulas mirror the design's `renderVals()`.
 */
import { boppPlan, flexoPlan, fmt, inr, lamPlan, loomPlan, num, plan, tapePlan } from '../../../engine';
import type { AppState, CalcResult, Sku, WastageAllowances } from '../../../engine';

const DASH = '—';

export type PlanState = Pick<AppState, 'glob' | 'machines'>;

/* ---------- shared planning helpers ---------- */

/** Loom / line counts are whole numbers ≥ 1. */
export const lineCountOf = (v: unknown): number => Math.max(1, Math.round(num(v)) || 1);
/** "+" stepper value. */
export const lineInc = (v: unknown): number => lineCountOf(v) + 1;
/** "−" stepper value (never below 1). */
export const lineDec = (v: unknown): number => Math.max(1, (Math.round(num(v)) || 1) - 1);
/** Working-hours placeholder: the global working hours/day (at least 1). */
export const hoursPlaceholder = (st: PlanState): string => String(Math.max(1, num(st.glob.workHours)));

const runtimeS = (wh: number, eff: number) => (eff > 0 ? fmt((wh * eff) / 100, 1) + ' hr/day' : DASH);
const daysS = (days: number) => (days > 0 ? fmt(days, 1) + ' days' : DASH);
const speedS = (v: number) => (v > 0 ? fmt(v, 0) + ' m/min' : DASH);
const perDayS = (v: number) => (v > 0 ? fmt(v, 0) + ' m/day' : DASH);

/* ---------- derived strip ---------- */

export interface DerivedView {
  denS: string;
  mdenS: string;
  lamDenS: string;
  showLamDen: boolean;
  gsmS: string;
  gpmUnlamS: string;
  gpmLamS: string;
  fabGramS: string;
  gpmS: string;
  cutS: string;
  netS: string;
  wtS: string;
  /** machine denier outside the usual range */
  denWarn: boolean;
  /** GSM zero or outside 40–200 */
  gsmBad: boolean;
  /** add-ons outweigh the target weight */
  wtBad: boolean;
  chainNote: string;
}

export function derivedView(s: Sku, r: CalcResult): DerivedView {
  return {
    denS: fmt(r.warpD, 0) + (s.mode !== 'wt' && num(s.warpD) !== num(s.weftD) ? ' / ' + fmt(r.weftD, 0) : ''),
    mdenS: fmt(r.machD, 0),
    lamDenS: r.hasLam ? fmt(r.lamD, 0) : DASH,
    showLamDen: r.hasLam,
    gsmS: fmt(r.gsm, 1),
    gpmUnlamS: fmt(r.gpmUnlam, 1) + ' g',
    gpmLamS: fmt(r.gpmLam, 1) + ' g',
    fabGramS: fmt(r.fabGram, 2),
    gpmS: fmt(r.gpmTotal, 1) + ' g',
    cutS: fmt(r.cutIn, 2) + '″',
    netS: fmt(r.net, 1),
    wtS: fmt(r.totalG, 1),
    denWarn: r.warpD > 0 && (r.warpD < 300 || r.warpD > 2500),
    gsmBad: r.gsm <= 0 || r.gsm < 40 || r.gsm > 200,
    wtBad: r.net <= 0,
    chainNote:
      'Chain: net wt ÷ cut length × 39.37 = meter weight → − BOPP/metallic → − lam GPM → unlam GPM → GSM = GPM × 39.37 ÷ (2 × width) → machine denier = GSM × 9000 ÷ ((warp mesh + weft mesh) × 39.37) → unlam denier = machine denier − ' +
      String(s.shrink) +
      '%. Net wt = bag wt − handle − liner − hem/bottom stitch grams (auto).',
  };
}

/* ---------- quantities strip ---------- */

export interface QuantitiesView {
  fabAllowS: string;
  lamAllowS: string;
  boppAllowS: string;
  fabMS: string;
  fabKgS: string;
  hasLam: boolean;
  lamMS: string;
  lamKgS: string;
  showBopp: boolean;
  boppMS: string;
  boppKgS: string;
  balesS: string;
}

export function quantitiesView(s: Sku, r: CalcResult, wa: WastageAllowances): QuantitiesView {
  return {
    fabAllowS: fmt(num(wa.fabAllow), 1),
    lamAllowS: fmt(num(wa.lamAllow), 1),
    boppAllowS: fmt(num(wa.boppAllow), 1),
    fabMS: fmt(r.fabricM, 0) + ' m',
    fabKgS: fmt(r.fabricKg, 0) + ' kg',
    hasLam: r.hasLam,
    lamMS: fmt(r.lamM, 0) + ' m',
    lamKgS: fmt(r.lamKg, 0) + ' kg',
    showBopp: s.boppVariant !== 'none',
    boppMS: fmt(r.boppM, 0) + ' m',
    boppKgS: fmt(r.boppKg, 0) + ' kg',
    balesS: r.bales > 0 ? fmt(r.bales, 0) + ' bales of ' + fmt(num(s.bagsPerBale), 0) : DASH,
  };
}

/* ---------- loom planning ---------- */

export interface LoomView {
  summary: string;
  numLoom: number;
  /** PPM is required while the panel is open */
  ppmMissing: boolean;
  ringS: string;
  numWarpS: string;
  dmWeftS: string;
  hproS: string;
  tproS: string;
  numDaysS: string;
}

export function loomView(st: PlanState, s: Sku, r: CalcResult): LoomView {
  const lp = loomPlan(st, s, r);
  const looms = fmt(lp.nl, 0) + ' loom' + (lp.nl > 1 ? 's' : '');
  return {
    summary: lp.tpro > 0 ? looms + ' · ' + fmt(lp.numDays, 1) + ' days' : 'Enter loom PPM',
    numLoom: lineCountOf(s.numLoom),
    ppmMissing: !!s.loomOpen && !(num(s.loomPpm) > 0),
    ringS: lp.ring > 0 ? fmt(lp.ring, 0) + ' mm' : DASH,
    numWarpS: lp.numWarp > 0 ? fmt(lp.numWarp, 0) + ' tapes' : DASH,
    dmWeftS: lp.dmWeft > 0 ? fmt(lp.dmWeft, 1) : DASH,
    hproS: lp.hpro > 0 ? fmt(lp.hpro, 0) + ' m / loom' : DASH,
    tproS: lp.tpro > 0 ? fmt(lp.tpro, 0) + ' m / loom' : DASH,
    numDaysS: lp.numDays > 0 ? fmt(lp.numDays, 1) + ' days · ' + looms : DASH,
  };
}

/* ---------- tape plant planning ---------- */

export interface TapeView {
  summary: string;
  lines: number;
  /** select value for the tape width */
  tapeWidth: string;
  effMissing: boolean;
  tapeDenierS: string;
  spacerS: string;
  usefulWS: string;
  tapeCountS: string;
  tapeCountSub: string;
  overWinders: boolean;
  winderNote: string;
  prodS: string;
  effOutS: string;
  runtimeS: string;
  tapeKgS: string;
  daysS: string;
}

export function tapeView(st: PlanState, s: Sku, r: CalcResult): TapeView {
  const tp = tapePlan(st, s, r);
  return {
    summary: tp.days > 0 ? fmt(tp.nt, 0) + ' tapes · ' + fmt(tp.prod, 0) + ' kg/hr · ' + fmt(tp.days, 1) + ' days' : 'Set line speed',
    lines: tp.lines,
    tapeWidth: String(s.tapeWidth || 2.45),
    effMissing: !!s.tapeOpen && !(num(s.tapeEff) > 0),
    tapeDenierS: r.machD > 0 ? fmt(r.machD, 0) + ' D' : DASH,
    spacerS: tp.spacer > 0 ? fmt(tp.spacer, 2) + ' mm' : DASH,
    usefulWS: tp.usefulW > 0 ? fmt(tp.usefulW, 1) + ' mm' : DASH,
    tapeCountS: tp.nt > 0 ? fmt(tp.nt, 0) : DASH,
    tapeCountSub: tp.usefulW > 0 ? 'useful ÷ spacer' : '',
    overWinders: tp.overWinders,
    winderNote: tp.overWinders
      ? 'Exceeds ' + fmt(tp.winders, 0) + ' winders'
      : tp.winders > 0 && tp.nt > 0
        ? fmt(tp.winders - tp.nt, 0) + ' winders free'
        : '',
    prodS: tp.prod > 0 ? fmt(tp.prod, 0) + ' kg/hr' : DASH,
    effOutS: tp.effOut > 0 ? fmt(tp.effOut, 0) + ' kg/hr' : DASH,
    runtimeS: runtimeS(tp.wh, tp.eff),
    tapeKgS: fmt(r.fabricKg, 0) + ' kg',
    daysS: daysS(tp.days),
  };
}

/* ---------- lamination planning ---------- */

export interface LamView {
  summary: string;
  hasLam: boolean;
  lines: number;
  effMissing: boolean;
  lamMS: string;
  passesS: string;
  coatGmS: string;
  runMS: string;
  effSpeedS: string;
  runtimeS: string;
  mPerDayS: string;
  coatKgHrS: string;
  coatKgS: string;
  daysS: string;
}

export function lamView(st: PlanState, s: Sku, r: CalcResult): LamView {
  const lm = lamPlan(st, s, r);
  return {
    summary: !lm.hasLam ? 'No lamination' : lm.days > 0 ? fmt(lm.runM, 0) + ' m · ' + fmt(lm.days, 1) + ' days' : 'Set line speed',
    hasLam: lm.hasLam,
    lines: lm.lines,
    effMissing: !!s.lamPlanOpen && !(num(s.lamPlanEff) > 0),
    lamMS: lm.hasLam ? fmt(r.lamM, 0) + ' m' : DASH,
    passesS: lm.hasLam ? (lm.passes === 2 ? '2 (double side)' : '1 (single side)') : DASH,
    coatGmS: lm.hasLam ? fmt(r.coatBuyGpm, 1) + ' g/m' : DASH,
    runMS: lm.hasLam ? fmt(lm.runM, 0) + ' m' : DASH,
    effSpeedS: speedS(lm.effSpeed),
    runtimeS: runtimeS(lm.wh, lm.eff),
    mPerDayS: perDayS(lm.mPerDay),
    coatKgHrS: lm.coatKgHr > 0 ? fmt(lm.coatKgHr, 0) + ' kg/hr' : DASH,
    coatKgS: lm.hasLam ? fmt(r.lamKg, 0) + ' kg' : DASH,
    daysS: daysS(lm.days),
  };
}

/* ---------- flexo printing planning ---------- */

export interface FlexoView {
  summary: string;
  applic: boolean;
  lines: number;
  effMissing: boolean;
  speedPh: string;
  recS: string;
  colS: string;
  speedS: string;
  metersS: string;
  effSpeedS: string;
  runtimeS: string;
  mPerDayS: string;
  daysS: string;
}

export function flexoView(st: PlanState, s: Sku, r: CalcResult): FlexoView {
  const fp = flexoPlan(st, s, r);
  return {
    summary: !fp.applic ? 'Not a flexo job' : fp.days > 0 ? fmt(fp.speed, 0) + ' m/min · ' + fmt(fp.days, 1) + ' days' : 'Set speed',
    applic: fp.applic,
    lines: fp.lines,
    effMissing: !!s.flexoOpen && !(num(s.flexoEff) > 0),
    speedPh: fmt(fp.rec, 0),
    recS: fmt(fp.rec, 0) + ' m/min for ' + fmt(fp.totCol, 0) + ' col',
    colS: fp.totCol > 0 ? fmt(fp.totCol, 0) + ' col' : DASH,
    speedS: speedS(fp.speed),
    metersS: fp.applic ? fmt(fp.meters, 0) + ' m' : DASH,
    effSpeedS: speedS(fp.effSpeed),
    runtimeS: runtimeS(fp.wh, fp.eff),
    mPerDayS: perDayS(fp.mPerDay),
    daysS: daysS(fp.days),
  };
}

/* ---------- BOPP printing planning ---------- */

export interface BoppView {
  summary: string;
  applic: boolean;
  lines: number;
  effMissing: boolean;
  speedPh: string;
  speedS: string;
  metersS: string;
  effSpeedS: string;
  runtimeS: string;
  mPerDayS: string;
  daysS: string;
}

export function boppView(st: PlanState, s: Sku, r: CalcResult): BoppView {
  const bp = boppPlan(st, s, r);
  return {
    summary: !bp.applic ? 'No BOPP film' : bp.days > 0 ? fmt(bp.speed, 0) + ' m/min · ' + fmt(bp.days, 1) + ' days' : 'Set speed',
    applic: bp.applic,
    lines: bp.lines,
    effMissing: !!s.boppPlanOpen && !(num(s.boppEff) > 0),
    speedPh: '140 (ARC) / 120 manual',
    speedS: speedS(bp.speed),
    // the design binds the order-quantity string here (its later `boppMS` key wins)
    metersS: fmt(r.boppM, 0) + ' m',
    effSpeedS: speedS(bp.effSpeed),
    runtimeS: runtimeS(bp.wh, bp.eff),
    mPerDayS: perDayS(bp.mPerDay),
    daysS: daysS(bp.days),
  };
}

/* ---------- BOM, conversion, totals ---------- */

export interface CostView {
  bom: { name: string; g: string; kg: string; rate: string; cost: string }[];
  conv: { name: string; basis: string; cost: string }[];
  matS: string;
  convTS: string;
  ovhPctS: string;
  ovhS: string;
  costBagS: string;
  costKgS: string;
  costMS: string;
  marginPctS: string;
  marginS: string;
  marginNegative: boolean;
  quoteS: string;
  qtyS: string;
  qtyKgS: string;
  orderValS: string;
  gstPctS: string;
  gstS: string;
  grandS: string;
}

export function costView(r: CalcResult, glob: AppState['glob']): CostView {
  return {
    bom: r.bom.map((b) => ({ name: b.name, g: fmt(b.g, 2), kg: fmt(b.kg, 0), rate: fmt(b.rate, 0), cost: inr(b.cost) })),
    conv: r.conv.map((c) => ({ name: c.name, basis: c.basis, cost: inr(c.cost) })),
    matS: inr(r.matCost),
    convTS: inr(r.convCost),
    ovhPctS: fmt(num(glob.overhead), 0),
    ovhS: inr(r.ovh),
    costBagS: inr(r.costBag),
    costKgS: inr(r.costKg),
    costMS: inr(r.costM),
    marginPctS: fmt(r.marginPct, 0),
    marginS: inr(r.marginAmt),
    marginNegative: r.marginPct < 0,
    quoteS: inr(r.quote),
    qtyS: fmt(r.qty, 0),
    qtyKgS: fmt((r.qty * r.totalG) / 1000, 0),
    orderValS: inr(r.orderVal, 0),
    gstPctS: fmt(num(glob.gst), 0),
    gstS: inr(r.gstAmt, 0),
    grandS: inr(r.grand, 0),
  };
}

/* ---------- plan strip ---------- */

export interface PlanStripRow {
  stage: string;
  req: string;
  cap: string;
  days: string;
  /** bottleneck stage, or a stage with no capacity set */
  flag: 'bottleneck' | 'noCapacity' | null;
}

export interface PlanStripView {
  rows: PlanStripRow[];
  note: string;
}

export function planStripView(st: PlanState, s: Sku, r: CalcResult): PlanStripView {
  const pl = plan(st, s, r);
  return {
    rows: pl.rows.map((p) => ({
      stage: p.stage,
      req: p.req,
      cap: p.cap,
      days: Number.isFinite(p.days) ? fmt(p.days, 1) + ' d' : DASH,
      flag: p.stage === pl.bottleneck ? 'bottleneck' : p.capN <= 0 ? 'noCapacity' : null,
    })),
    note: pl.bottleneck
      ? 'Bottleneck: ' + pl.bottleneck + ' — ' + fmt(pl.maxDays, 1) + ' working days at ' + pl.wh + ' h/day.'
      : 'Add machines on the Machines page to see scheduling.',
  };
}
