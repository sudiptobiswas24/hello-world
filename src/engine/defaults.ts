import type { AppState, BagType, FoldType, Sku, TypeConfig } from './types';

export const TYPE_CONFIG: Record<BagType, TypeConfig> = {
  plain: { label: 'Plain PP woven', lamSide: 'none', bopp: 'none', printed: 'no', extras: [], aLam: false, aBopp: false, aMet: false, aPrint: true, aLiner: true, aHandle: true },
  laminated: { label: 'Laminated PP', lamSide: 'double', bopp: 'none', printed: 'no', extras: [], aLam: true, aBopp: false, aMet: false, aPrint: true, aLiner: true, aHandle: true },
  bopp: { label: 'BOPP woven bag', lamSide: 'double', bopp: 'SS', printed: 'yes', extras: [], aLam: true, aBopp: true, aMet: true, aPrint: true, aLiner: true, aHandle: true },
  leno: { label: 'Leno bag', lamSide: 'none', bopp: 'none', printed: 'no', extras: [], aLam: false, aBopp: false, aMet: false, aPrint: true, aLiner: true, aHandle: false },
  valve: { label: 'Valve bag', lamSide: 'double', bopp: 'none', printed: 'yes', extras: ['valveAt'], aLam: true, aBopp: false, aMet: false, aPrint: true, aLiner: true, aHandle: true },
  box: { label: 'Box type bag', lamSide: 'double', bopp: 'SS', printed: 'yes', extras: [], aLam: true, aBopp: true, aMet: true, aPrint: true, aLiner: true, aHandle: true },
};

export const isBagType = (t: unknown): t is BagType =>
  typeof t === 'string' && Object.prototype.hasOwnProperty.call(TYPE_CONFIG, t);

/** Bottom-fold allowance in inches, added to cut length. */
export const FOLD_ALLOWANCE: Record<FoldType, number> = { SFSS: 1.25, SFDS: 1.25, DFSS: 3.25, DFDS: 3.25, EZWF: 1.25, EZWOF: 0.75 };

export const isFoldType = (t: unknown): t is FoldType =>
  typeof t === 'string' && Object.prototype.hasOwnProperty.call(FOLD_ALLOWANCE, t);

export const BLANK_SKU: Omit<Sku, 'id' | 'name' | 'type' | 'width' | 'length'> = {
  jobId: '', party: '', date: '', unit: 'in', gussetSide: 'none', gusset: 0,
  ashPct: '', yarnColor: '', yarnDenier: '',
  loomPpm: '', loomEff: 85, loomHours: '', numLoom: 1, loomOpen: false,
  tapeOpen: false, tapeOutput: 600, tapeDieW: 1600, tapeOutW: 1450, tapeWidth: 2.45, tapeEdge: 4, tapeEff: 85, tapeDraw: 6, tapeSpeed: 350, tapeHours: '', numTapeLine: 1, winders: 360,
  lamPlanOpen: false, lamSpeed: 120, lamPlanEff: 85, lamHours: '', numLamLine: 1,
  flexoOpen: false, flexoSpeed: '', flexoEff: 85, flexoHours: '', numFlexoLine: 1,
  boppPlanOpen: false, boppSpeed: '', boppEff: 85, boppHours: '', numBoppLine: 1,
  foldType: 'SFDS', fold: 1.25, hemOn: false, hemAllow: 0.5,
  warpMesh: 10, weftMesh: 10, mode: 'wt', targetWt: 70, warpD: 900, weftD: 900, shrink: 4,
  lamSide: 'none', boppVariant: 'none', boppW: 515, boppMic: 10, finish: 'glossy',
  metallic: 'none', metW: 410, metMic: 15,
  printed: 'no', colors: 2, printSides: 'single', colorsBack: 1,
  inkSel: ['inkWhite'],
  tapeMix: [{ m: 'pp', wt: 96 }, { m: 'colmb', wt: 2 }, { m: 'cc', wt: 2 }],
  lamMix: [{ m: 'lampp', wt: 80 }, { m: 'ldpe1070', wt: 20 }],
  fabricType: 'milky', window: 'none', pattern: 'none', fabricColor: '', mbCode: '',
  lenoStitch: 'corner',
  boxStyle: 'dcut',
  linerOn: false, linerG: 0, linerMic: 40, linerWmm: 0, linerLmm: 0,
  handleOn: false, handleWt: 2, handleColor: '',
  lamInMode: 'gpm', lamVal: '',
  bagsPerBale: 500,
  strength: '', elong: '', qty: 50000, marginOv: '', inkCodes: {}, fillKg: '', job: {},
};

const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

/** A fully-populated spec: blank defaults overlaid with `o`. */
export function makeSku(o: Partial<Sku> & Pick<Sku, 'id' | 'name'>): Sku {
  return Object.assign({ type: 'plain', width: '', length: '' }, clone(BLANK_SKU), o) as Sku;
}

export function sampleSkus(): Sku[] {
  return [
    makeSku({ id: 's1', name: '25 kg rice sack', type: 'plain', jobId: 'JOB-101', party: 'Annapurna Agro', width: 18, length: 30, targetWt: 68, qty: 50000 }),
    makeSku({ id: 's2', name: '10 kg flour bag', type: 'bopp', jobId: 'JOB-102', party: 'Shree Mills', width: 20, length: 28, warpMesh: 10, weftMesh: 10, mode: 'wt', targetWt: 60, lamSide: 'double', lamVal: 12, boppVariant: 'SS', boppW: 515, boppMic: 10, printed: 'yes', colors: 6, qty: 100000 }),
    makeSku({ id: 's3', name: '50 kg cement valve bag', type: 'valve', jobId: 'JOB-103', party: 'Deccan Cements', unit: 'cm', width: 50, length: 70, foldType: 'DFDS', fold: 2, targetWt: 76, lamSide: 'double', lamVal: 13, printed: 'yes', colors: 2, qty: 200000 }),
    makeSku({ id: 's4', name: 'Onion leno 25 kg', type: 'leno', jobId: 'JOB-104', party: 'Nashik Traders', unit: 'cm', width: 55, length: 80, mode: 'dn', warpMesh: 4, weftMesh: 4, warpD: 800, weftD: 800, qty: 30000 }),
    makeSku({ id: 's5', name: 'D-cut shopper', type: 'box', boxStyle: 'dcut', jobId: 'JOB-105', party: 'Retail pack', width: 12, length: 16, mode: 'dn', warpMesh: 12, weftMesh: 12, warpD: 500, weftD: 500, lamSide: 'double', lamVal: 10, boppVariant: 'SS', boppW: 340, boppMic: 12, printed: 'yes', colors: 4, qty: 20000 }),
  ];
}

export function defaultMaterialSections(): AppState['matSections'] {
  return [
    { id: 'tape', label: 'Tape plant', items: [
      { id: 'pp', name: 'PP granules', rate: 102 }, { id: 'cc', name: 'CC (CaCO₃ filler)', rate: 28 },
      { id: 'tpt', name: 'TPT', rate: 120 }, { id: 'omega', name: 'Omega', rate: 110 },
      { id: 'colmb', name: 'Colour master batch', rate: 165 } ] },
    { id: 'lam', label: 'Lamination', items: [
      { id: 'lampp', name: 'Lam PP', rate: 104 }, { id: 'lamcc', name: 'Lam CC', rate: 28 },
      { id: 'lamtpt', name: 'TPT', rate: 120 }, { id: 'ldpe040', name: 'LDPE-040', rate: 98 },
      { id: 'ldpe1070', name: 'LDPE-1070', rate: 96 } ] },
    { id: 'print', label: 'Printing (Flexo)', items: [
      { id: 'inkWhite', name: 'Ink — white', rate: 230 }, { id: 'inkBlack', name: 'Ink — black', rate: 240 },
      { id: 'inkGreen', name: 'Ink — green', rate: 265 }, { id: 'inkOrange', name: 'Ink — orange', rate: 260 },
      { id: 'inkBrown', name: 'Ink — brown', rate: 255 }, { id: 'inkBlue', name: 'Ink — blue', rate: 260 },
      { id: 'inkRed', name: 'Ink — red', rate: 260 }, { id: 'inkYellow', name: 'Ink — yellow', rate: 255 },
      { id: 'inkMagenta', name: 'Ink — magenta', rate: 270 }, { id: 'inkCyan', name: 'Ink — cyan blue', rate: 265 },
      { id: 'reducer', name: 'Reducer', rate: 140 }, { id: 'mibk', name: 'MIBK', rate: 190 } ] },
    { id: 'boppSec', label: 'BOPP section', items: [
      { id: 'boppFilm', name: 'BOPP film', rate: 118 }, { id: 'metFilm', name: 'Metallic / window film', rate: 145 },
      { id: 'binkWhite', name: 'Ink — white', rate: 235 }, { id: 'binkBlack', name: 'Ink — black', rate: 245 },
      { id: 'binkCyan', name: 'Ink — cyan blue', rate: 270 }, { id: 'binkYellow', name: 'Ink — yellow', rate: 260 },
      { id: 'binkRed', name: 'Ink — red', rate: 265 }, { id: 'binkGreen', name: 'Ink — green', rate: 270 },
      { id: 'binkMagenta', name: 'Ink — magenta', rate: 275 }, { id: 'binkOrange', name: 'Ink — orange', rate: 265 },
      { id: 'binkViolet', name: 'Ink — violet', rate: 280 }, { id: 'binkMatMedium', name: 'Mat medium', rate: 250 } ] },
    { id: 'finish', label: 'Finishing', items: [
      { id: 'thread', name: 'Stitching thread', rate: 160 }, { id: 'liner', name: 'LDPE liner', rate: 96 },
      { id: 'handle', name: 'Handle strip', rate: 110 }, { id: 'baling', name: 'Baling / packing', rate: 60 } ] },
  ];
}

export function defaultState(): AppState {
  return {
    tab: 'calc', layout: null, selId: 's1', qcLog: [],
    skus: sampleSkus(),
    mats: { pp: 102, mb: 165, filler: 28, lamCoat: 108, bopp: 118, liner: 96, ink: 240, thread: 160 },
    matSections: defaultMaterialSections(),
    rateHistory: [],
    convs: { tape: 7, weaving: 6, lamination: 4.5, printing: 5, cutting: 4, linerins: 0.30, dcut: 0.25, handleAt: 0.50, valveAt: 0.35, packing: 0.15 },
    wast: { fabAllow: 3, lamAllow: 1.5, boppAllow: 1, lamDeductF: 0.55 },
    glob: { mixPP: 96, mixMB: 2, mixFiller: 2, overhead: 8, margin: 12, gst: 18, inkFactor: 0.5, inkCoverage: 100, workHours: 20, roundM: 100, redPct: 20, mibkPct: 5, stitchDm: 12.5, threadFactor: 4.5 },
    machines: [
      { id: 'm1', name: 'Tape line — Lohia', typ: 'tape', p: { speed: 300, tapes: 80 }, count: 1, eff: 90 },
      { id: 'm2', name: 'Circular looms', typ: 'loom', p: { rpm: 90, shuttles: 6 }, count: 16, eff: 85 },
      { id: 'm3', name: 'Extrusion lamination', typ: 'lamination', p: { speed: 100 }, count: 1, eff: 60 },
      { id: 'm4', name: 'Flexo printer', typ: 'printing', p: { bph: 3000 }, count: 2, eff: 80 },
      { id: 'm5', name: 'BCS line', typ: 'cutting', p: { bph: 1800 }, count: 3, eff: 85 },
    ],
    quote: { customer: '', gstin: '', sel: { s1: true, s2: true } },
  };
}
