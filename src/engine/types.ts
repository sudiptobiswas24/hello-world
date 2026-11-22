/**
 * Domain types for the PP woven sacks costing engine.
 *
 * Input-backed fields are `NumInput` because form inputs hand back strings
 * (and may be blank); the engine always reads them through `num()`.
 */
export type NumInput = number | string;

export type BagType = 'plain' | 'laminated' | 'bopp' | 'leno' | 'valve' | 'box';
export type Unit = 'in' | 'cm' | 'mm';
export type FoldType = 'SFSS' | 'SFDS' | 'DFSS' | 'DFDS' | 'EZWF' | 'EZWOF';
export type CalcMode = 'wt' | 'dn';
export type LamSide = 'none' | 'single' | 'double';
export type BoppVariant = 'none' | 'SS' | 'BS';
/** 'window' film is costed at half weight; 'metallic' is a legacy value treated like 'full'. */
export type Metallic = 'none' | 'window' | 'full' | 'metallic';
export type YesNo = 'yes' | 'no';
export type Layout = 'sheet' | 'split' | 'cards';
export type Tab = 'calc' | 'rates' | 'settings' | 'mach' | 'job' | 'quote';

export interface MixRow {
  m: string;
  wt: NumInput;
}

export interface Sku {
  id: string;
  name: string;
  type: BagType;
  jobId: string;
  party: string;
  date: string;
  unit: Unit;
  width: NumInput;
  length: NumInput;
  gussetSide: 'none' | 'single' | 'both';
  gusset: NumInput;
  ashPct: NumInput;
  yarnColor: string;
  yarnDenier: NumInput;
  // loom planning
  loomPpm: NumInput;
  loomEff: NumInput;
  loomHours: NumInput;
  numLoom: NumInput;
  loomOpen: boolean;
  // tape planning
  tapeOpen: boolean;
  tapeOutput: NumInput;
  tapeDieW: NumInput;
  tapeOutW: NumInput;
  tapeWidth: NumInput;
  tapeEdge: NumInput;
  tapeEff: NumInput;
  tapeDraw: NumInput;
  tapeSpeed: NumInput;
  tapeHours: NumInput;
  numTapeLine: NumInput;
  winders: NumInput;
  // lamination planning
  lamPlanOpen: boolean;
  lamSpeed: NumInput;
  lamPlanEff: NumInput;
  lamHours: NumInput;
  numLamLine: NumInput;
  // flexo planning
  flexoOpen: boolean;
  flexoSpeed: NumInput;
  flexoEff: NumInput;
  flexoHours: NumInput;
  numFlexoLine: NumInput;
  // BOPP planning
  boppPlanOpen: boolean;
  boppSpeed: NumInput;
  boppEff: NumInput;
  boppHours: NumInput;
  numBoppLine: NumInput;
  // construction
  foldType: FoldType;
  fold: NumInput;
  hemOn: boolean;
  hemAllow: NumInput;
  warpMesh: NumInput;
  weftMesh: NumInput;
  mode: CalcMode;
  targetWt: NumInput;
  warpD: NumInput;
  weftD: NumInput;
  shrink: NumInput;
  lamSide: LamSide;
  boppVariant: BoppVariant;
  boppW: NumInput;
  boppMic: NumInput;
  finish: 'glossy' | 'matt' | 'metallic';
  metallic: Metallic;
  metW: NumInput;
  metMic: NumInput;
  printed: YesNo;
  colors: NumInput;
  printSides: 'single' | 'both';
  colorsBack: NumInput;
  inkSel: string[];
  tapeMix: MixRow[];
  lamMix: MixRow[];
  fabricType: 'milky' | 'natural';
  window: 'none' | 'offcentre' | 'middle';
  pattern: 'none' | 'checker' | 'sideBorder' | 'rangoli';
  fabricColor: string;
  mbCode: string;
  lenoStitch: string;
  boxStyle: 'dcut' | 'handleLoop' | string;
  linerOn: boolean;
  linerG: NumInput;
  linerMic: NumInput;
  linerWmm: NumInput;
  linerLmm: NumInput;
  handleOn: boolean;
  handleWt: NumInput;
  handleColor: string;
  lamInMode: 'gpm';
  lamVal: NumInput;
  bagsPerBale: NumInput;
  strength: NumInput;
  elong: NumInput;
  qty: NumInput;
  marginOv: NumInput;
  inkCodes: Record<string, string>;
  fillKg: NumInput;
  job: Record<string, string>;
  images?: string[];
  locked?: boolean;
  savedAt?: string;
  /** legacy fields that older saves may carry */
  [legacy: string]: unknown;
}

export interface MaterialItem {
  id: string;
  name: string;
  rate: NumInput;
}

export interface MaterialSection {
  id: string;
  label: string;
  items: MaterialItem[];
}

export interface RateHistoryEntry {
  /** material id */
  m: string;
  /** date the entry was recorded (YYYY-MM-DD) */
  d: string;
  /** date the rate takes effect (YYYY-MM-DD) */
  eff?: string;
  r: NumInput;
}

export interface QcLogEntry {
  cc: NumInput;
  gpd?: NumInput;
  el?: NumInput;
  [k: string]: unknown;
}

export interface ConversionRates {
  tape: NumInput;
  weaving: NumInput;
  lamination: NumInput;
  printing: NumInput;
  cutting: NumInput;
  linerins: NumInput;
  dcut: NumInput;
  handleAt: NumInput;
  valveAt: NumInput;
  packing: NumInput;
}

export interface WastageAllowances {
  fabAllow: NumInput;
  lamAllow: NumInput;
  boppAllow: NumInput;
  lamDeductF: NumInput;
}

export interface GlobalSettings {
  mixPP: NumInput;
  mixMB: NumInput;
  mixFiller: NumInput;
  overhead: NumInput;
  margin: NumInput;
  gst: NumInput;
  inkFactor: NumInput;
  inkCoverage: NumInput;
  workHours: NumInput;
  roundM: NumInput;
  redPct: NumInput;
  mibkPct: NumInput;
  stitchDm: NumInput;
  threadFactor: NumInput;
}

export type MachineType = 'tape' | 'loom' | 'lamination' | 'printing' | 'cutting';

export interface Machine {
  id: string;
  name: string;
  typ: MachineType;
  p: Record<string, NumInput>;
  count: NumInput;
  eff: NumInput;
}

export interface QuoteState {
  customer: string;
  gstin: string;
  sel: Record<string, boolean>;
  [k: string]: unknown;
}

export interface AppState {
  tab: Tab;
  layout: Layout | null;
  selId: string | null;
  qcLog: QcLogEntry[];
  skus: Sku[];
  /** legacy flat rates (pre-sections); kept for migration */
  mats: Record<string, NumInput>;
  matSections: MaterialSection[];
  rateHistory: RateHistoryEntry[];
  convs: ConversionRates;
  wast: WastageAllowances;
  glob: GlobalSettings;
  machines: Machine[];
  quote: QuoteState;
  // transient UI state that the design also persisted
  tableExpand?: boolean;
  histSel?: string;
  bulkOpen?: boolean;
  bulkEff?: string;
  bulkDraft?: Record<string, NumInput>;
  bulkFlash?: number;
  rateDraft?: Record<string, NumInput>;
  planSel?: string;
  jobSel?: string;
  [k: string]: unknown;
}

export interface TypeConfig {
  label: string;
  lamSide: LamSide;
  bopp: BoppVariant;
  printed: YesNo;
  extras: (keyof ConversionRates)[];
  aLam: boolean;
  aBopp: boolean;
  aMet: boolean;
  aPrint: boolean;
  aLiner: boolean;
  aHandle: boolean;
}
