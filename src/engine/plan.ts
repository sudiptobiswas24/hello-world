import type { CalcResult } from './calc';
import { fmt, num } from './format';
import type { AppState, Sku } from './types';

type PlanState = Pick<AppState, 'glob' | 'machines'>;

const hoursOr = (st: PlanState, v: Sku[keyof Sku]) => (num(v) > 0 ? num(v) : Math.max(1, num(st.glob.workHours)));
const lineCount = (v: Sku[keyof Sku]) => Math.max(1, Math.round(num(v)) || 1);

export interface LoomPlan {
  /** loom ring diameter, mm */
  ring: number;
  numWarp: number;
  /** weft picks per decimetre */
  dmWeft: number;
  /** metres per working day per loom @100% */
  hpro: number;
  /** metres per working day per loom at efficiency */
  tpro: number;
  nl: number;
  wh: number;
  eff: number;
  numDays: number;
}

export function loomPlan(st: PlanState, s: Sku, r: CalcResult): LoomPlan {
  const lw = num(s.width);
  const wm2 = num(s.warpMesh) || 10;
  const fm2 = num(s.weftMesh) || 10;
  let ring = 0;
  let numWarp = 0;
  if (s.unit === 'cm') {
    ring = (lw * 2 * 10) / 3.142;
    numWarp = ((lw * 2) / 2.54) * wm2;
  } else if (s.unit === 'mm') {
    ring = (lw * 2) / 3.142;
    numWarp = ((lw * 2) / 25.4) * wm2;
  } else {
    ring = (lw * 2 * 25.4) / 3.142;
    numWarp = lw * 2 * wm2;
  }
  const dmWeft = fm2 / 0.254;
  const ppm = Math.max(0, num(s.loomPpm));
  const eff = Math.max(0, num(s.loomEff));
  const wh = hoursOr(st, s.loomHours);
  const hpro = fm2 > 0 ? (ppm * 60 * wh) / (39.37 * fm2) : 0;
  const tpro = (hpro * eff) / 100;
  const nl = lineCount(s.numLoom);
  const numDays = tpro > 0 && r.fabricM > 0 ? r.fabricM / (nl * tpro) : 0;
  return { ring, numWarp, dmWeft, hpro, tpro, nl, wh, eff, numDays };
}

export interface TapePlan {
  D: number;
  tw: number;
  filmW: number;
  ratio: number;
  spacer: number;
  usefulW: number;
  nt: number;
  speed: number;
  prod: number;
  eff: number;
  effOut: number;
  lines: number;
  wh: number;
  days: number;
  winders: number;
  overWinders: boolean;
}

/** Tape line: spacer = √(stretch ratio) × tape width; output kg/hr = denier × speed × tapes ÷ 150000. */
export function tapePlan(st: PlanState, s: Sku, r: CalcResult): TapePlan {
  const D = r.machD;
  const tw = num(s.tapeWidth) || 2.45;
  const filmW = Math.max(0, num(s.tapeOutW)) || 1450;
  const ratio = Math.max(0, num(s.tapeDraw));
  const spacer = ratio > 0 && tw > 0 ? Math.sqrt(ratio) * tw : 0;
  const usefulW = Math.max(0, filmW - 2 * spacer);
  const nt = spacer > 0 ? Math.floor(usefulW / spacer) : 0;
  const speed = Math.max(0, num(s.tapeSpeed));
  const prod = D > 0 && speed > 0 && nt > 0 ? (D * speed * nt) / 150000 : 0;
  const eff = Math.max(0, num(s.tapeEff));
  const effOut = (prod * eff) / 100;
  const lines = lineCount(s.numTapeLine);
  const wh = hoursOr(st, s.tapeHours);
  const days = effOut > 0 && r.fabricKg > 0 ? r.fabricKg / (effOut * lines * wh) : 0;
  const winders = Math.max(0, Math.round(num(s.winders)));
  const overWinders = winders > 0 && nt > winders;
  return { D, tw, filmW, ratio, spacer, usefulW, nt, speed, prod, eff, effOut, lines, wh, days, winders, overWinders };
}

export interface LamPlan {
  hasLam: boolean;
  passes: number;
  runM: number;
  speed: number;
  eff: number;
  effSpeed: number;
  lines: number;
  wh: number;
  days: number;
  coatKgHr: number;
  mPerDay: number;
}

export function lamPlan(st: PlanState, s: Sku, r: CalcResult): LamPlan {
  const passes = s.lamSide === 'double' ? 2 : 1;
  const runM = r.lamM;
  const speed = Math.max(0, num(s.lamSpeed));
  const eff = Math.max(0, num(s.lamPlanEff));
  const effSpeed = (speed * eff) / 100;
  const lines = lineCount(s.numLamLine);
  const wh = hoursOr(st, s.lamHours);
  const days = effSpeed > 0 && runM > 0 ? runM / (effSpeed * 60 * lines * wh) : 0;
  const coatKgHr = r.coatBuyGpm > 0 && effSpeed > 0 ? (r.coatBuyGpm * effSpeed * 60) / 1000 : 0;
  const mPerDay = effSpeed * 60 * wh;
  return { hasLam: r.hasLam, passes, runM, speed, eff, effSpeed, lines, wh, days, coatKgHr, mPerDay };
}

export interface PrintPlan {
  applic: boolean;
  speed: number;
  eff: number;
  effSpeed: number;
  lines: number;
  wh: number;
  meters: number;
  mPerDay: number;
  days: number;
}

export interface FlexoPlan extends PrintPlan {
  totCol: number;
  /** recommended speed for the colour count, m/min */
  rec: number;
}

export function flexoPlan(st: PlanState, s: Sku, r: CalcResult): FlexoPlan {
  const applic = r.printed && s.type !== 'bopp';
  const totCol = num(s.colors) + (s.printSides === 'both' ? num(s.colorsBack) : 0);
  const rec = totCol <= 2 ? 70 : totCol <= 4 ? 62 : 55;
  const speed = num(s.flexoSpeed) > 0 ? num(s.flexoSpeed) : rec;
  const eff = Math.max(0, num(s.flexoEff));
  const effSpeed = (speed * eff) / 100;
  const lines = lineCount(s.numFlexoLine);
  const wh = hoursOr(st, s.flexoHours);
  const meters = r.fabricM;
  const mPerDay = effSpeed * 60 * wh;
  const days = applic && effSpeed > 0 && meters > 0 ? meters / (effSpeed * 60 * lines * wh) : 0;
  return { applic, totCol, rec, speed, eff, effSpeed, lines, wh, meters, mPerDay, days };
}

export function boppPlan(st: PlanState, s: Sku, r: CalcResult): PrintPlan {
  const applic = r.hasBopp;
  const speed = num(s.boppSpeed) > 0 ? num(s.boppSpeed) : 140;
  const eff = Math.max(0, num(s.boppEff));
  const effSpeed = (speed * eff) / 100;
  const lines = lineCount(s.numBoppLine);
  const wh = hoursOr(st, s.boppHours);
  const meters = r.boppM;
  const mPerDay = effSpeed * 60 * wh;
  const days = applic && effSpeed > 0 && meters > 0 ? meters / (effSpeed * 60 * lines * wh) : 0;
  return { applic, speed, eff, effSpeed, lines, wh, meters, mPerDay, days };
}

export interface PlanRow {
  stage: string;
  /** requirement, formatted */
  req: string;
  capN: number;
  cap: string;
  days: number;
  hours: number;
  lines: number;
}

export interface Plan {
  rows: PlanRow[];
  maxDays: number;
  bottleneck: string;
  wh: number;
}

/** Days per production stage for one order; the slowest finite stage is the bottleneck. */
export function plan(st: PlanState, s: Sku, r: CalcResult): Plan {
  const wh = Math.max(1, num(st.glob.workHours));
  const tp = tapePlan(st, s, r);
  const lp = loomPlan(st, s, r);
  const lm = lamPlan(st, s, r);
  const fp = flexoPlan(st, s, r);
  const bp = boppPlan(st, s, r);
  const rows: Omit<PlanRow, 'hours'>[] = [];
  rows.push({ stage: 'Tape extrusion', req: fmt(r.fabricKg, 0) + ' kg', capN: tp.effOut, cap: tp.effOut > 0 ? fmt(tp.effOut, 0) + ' kg/hr' : 'set line speed', days: tp.days, lines: tp.lines });
  rows.push({ stage: 'Weaving', req: fmt(r.fabricM, 0) + ' m', capN: lp.tpro, cap: lp.tpro > 0 ? fmt(lp.tpro, 0) + ' m/day/loom' : 'set loom PPM', days: lp.numDays, lines: lp.nl });
  if (r.hasLam) rows.push({ stage: 'Lamination', req: fmt(r.lamM, 0) + ' m', capN: lm.effSpeed, cap: lm.effSpeed > 0 ? fmt(lm.mPerDay, 0) + ' m/day' : 'set line speed', days: lm.days, lines: lm.lines });
  if (fp.applic) rows.push({ stage: 'Flexo printing', req: fmt(fp.meters, 0) + ' m', capN: fp.effSpeed, cap: fp.effSpeed > 0 ? fmt(fp.mPerDay, 0) + ' m/day' : 'set speed', days: fp.days, lines: fp.lines });
  if (bp.applic) rows.push({ stage: 'BOPP printing', req: fmt(bp.meters, 0) + ' m', capN: bp.effSpeed, cap: bp.effSpeed > 0 ? fmt(bp.mPerDay, 0) + ' m/day' : 'set speed', days: bp.days, lines: bp.lines });
  const bcs = st.machines.filter((m) => m.typ === 'cutting');
  const cutCap = bcs.reduce((a, m) => a + num(m.p.bph) * Math.max(1, num(m.count)) * (num(m.eff) / 100), 0);
  const cutDays = cutCap > 0 ? r.qty / cutCap / wh : Infinity;
  rows.push({ stage: 'Cutting + stitching', req: fmt(r.qty, 0) + ' bags', capN: cutCap, cap: cutCap > 0 ? fmt(cutCap, 0) + ' bags/hr' : 'no machine', days: cutDays, lines: bcs.length });
  let maxDays = 0;
  let bottleneck = '';
  const out: PlanRow[] = rows.map((x) => {
    if (Number.isFinite(x.days) && x.days > maxDays) {
      maxDays = x.days;
      bottleneck = x.stage;
    }
    return { ...x, hours: Number.isFinite(x.days) ? x.days * wh : Infinity };
  });
  return { rows: out, maxDays, bottleneck, wh };
}
