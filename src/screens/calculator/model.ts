/**
 * View-model helpers for the Calculator screen (list rows + spec form).
 * Each mirrors a formula from the design's `renderVals()`; nothing here
 * touches state — components apply the results through store actions.
 */
import { FOLD_ALLOWANCE, TYPE_CONFIG, fmt, inr, matGet, matName, mixPct, num } from '../../engine';
import type { AppState, BagType, CalcResult, FoldType, MixRow, NumInput, QcLogEntry, QcModel, Sku, Unit } from '../../engine';

export type InputToneName = 'default' | 'brand' | 'missing';
export type TextTone = 'ok' | 'warn' | 'subtle';

/** Design `reqInp(ok, brand)`: orange "missing" style until a required value is filled. */
export function reqTone(ok: boolean, brand = false): InputToneName {
  if (!ok) return 'missing';
  return brand ? 'brand' : 'default';
}

// ---------- bag type option lists ----------

/** Short labels used in the sheet table's type cell. */
export const TYPE_OPTIONS_SHORT: { value: BagType; label: string }[] = [
  { value: 'plain', label: 'Plain PP woven' },
  { value: 'laminated', label: 'Laminated PP' },
  { value: 'bopp', label: 'BOPP woven' },
  { value: 'leno', label: 'Leno' },
  { value: 'valve', label: 'Valve bag' },
  { value: 'box', label: 'Box type' },
];

/** Full labels used in the detail form's "Bag type" select. */
export const TYPE_OPTIONS_LONG: { value: BagType; label: string }[] = [
  { value: 'plain', label: 'Plain PP woven sack' },
  { value: 'laminated', label: 'Laminated PP woven bag' },
  { value: 'bopp', label: 'BOPP woven bag' },
  { value: 'leno', label: 'Leno bag' },
  { value: 'valve', label: 'Valve bag (stitched)' },
  { value: 'box', label: 'Box type bag' },
];

export const typeLabel = (t: BagType): string => (TYPE_CONFIG[t] || TYPE_CONFIG.plain).label;

// ---------- list rows ----------

export interface RowView {
  typeLabel: string;
  dimsS: string;
  modeLabel: string;
  gsmS: string;
  wtS: string;
  costS: string;
  quoteS: string;
  /** every issue message, one per line (row tooltip) */
  errTitle: string;
  /** row highlight: selected wins over "has errors" */
  tone: 'sel' | 'err' | 'none';
}

export function rowView(s: Sku, r: CalcResult, selected: boolean): RowView {
  return {
    typeLabel: typeLabel(s.type),
    dimsS: fmt(num(s.width), 1) + ' × ' + fmt(num(s.length), 1) + ' ' + s.unit,
    modeLabel: s.mode === 'wt' ? 'wt → GSM' : 'D → wt',
    gsmS: fmt(r.gsm, 0),
    wtS: fmt(r.totalG, 1),
    costS: inr(r.costBag),
    quoteS: inr(r.quote),
    errTitle: r.issues.length ? r.issues.map((i) => i.msg).join('\n') : '',
    tone: selected ? 'sel' : r.errCount > 0 ? 'err' : 'none',
  };
}

// ---------- dimensions ----------

const FOLD_NAMES: Record<FoldType, string> = {
  SFSS: 'SFSS', SFDS: 'SFDS', DFSS: 'DFSS', DFDS: 'DFDS', EZWF: 'Ez-Open with fold', EZWOF: 'Ez-Open w/o fold',
};

/** Bottom-fold options labelled with their allowance in the spec's unit. */
export function foldOptions(unit: Unit): { value: FoldType; label: string }[] {
  const cv = (v: number) => (unit === 'cm' ? fmt(v * 2.54, 2) + ' cm' : unit === 'mm' ? fmt(v * 25.4, 1) + ' mm' : fmt(v, 2) + '″');
  return (Object.keys(FOLD_NAMES) as FoldType[]).map((k) => ({ value: k, label: FOLD_NAMES[k] + ' (+' + cv(FOLD_ALLOWANCE[k]) + ')' }));
}

/** Width/length equivalent shown under the input ("= 457 mm"). */
export function unitEq(v: NumInput, unit: Unit): string {
  const n = num(v);
  if (!n) return '';
  if (unit === 'mm') return '= ' + fmt(n / 10, 1) + ' cm';
  if (unit === 'cm') return '= ' + fmt(n * 10, 0) + ' mm';
  return '= ' + fmt(n * 25.4, 0) + ' mm';
}

/** Echo line under the form: inches, mm, cut length and gusset. */
export function dimsEcho(s: Sku, r: CalcResult): string {
  return (
    '= ' + fmt(r.W, 1) + ' × ' + fmt(r.L, 1) + '″ · ' + fmt(r.W * 25.4, 0) + ' × ' + fmt(r.L * 25.4, 0) + ' mm · cut length ' +
    fmt(r.cutIn, 2) + '″ (' + fmt(r.cutIn * 25.4, 0) + ' mm)' +
    (r.Gz > 0 ? ' · gusset ' + fmt(r.Gz * (s.gussetSide === 'both' ? 2 : 1), 1) + '″ (' + s.gussetSide + ' side, job card only)' : '')
  );
}

// ---------- films ----------

export function boppWidthEq(boppW: NumInput): string {
  const n = num(boppW);
  return n ? '= ' + fmt(n / 10, 1) + ' cm · ' + fmt(n / 25.4, 2) + '″' : '';
}

/** Recommended BOPP film width = fabric width (mm) + 10. */
export function filmRec(s: Sku, r: CalcResult): { show: boolean; value: number; label: string } {
  const value = Math.round(r.W * 25.4 + 10);
  return {
    show: s.boppVariant !== 'none' && r.W > 0 && Math.abs(num(s.boppW) - value) > 0.5,
    value,
    label: fmt(value, 0),
  };
}

/** Recommended metallic film size: window = film − gusset, full = film + 10 mm. */
export function metRec(s: Sku): { show: boolean; value: number; label: string; why: string; title: string } {
  const isWindow = s.metallic === 'window';
  const gussetMm = s.gussetSide !== 'none' ? num(s.gusset) * (s.unit === 'cm' ? 10 : s.unit === 'mm' ? 1 : 25.4) : 0;
  const value = isWindow ? Math.round(num(s.boppW) - gussetMm) : Math.round(num(s.boppW) + 10);
  const show = s.metallic !== 'none' && num(s.boppW) > 0 && value > 0 && Math.abs(num(s.metW) - value) > 0.5;
  return {
    show,
    value,
    label: fmt(value, 0),
    why: isWindow ? '(film − gusset)' : '(film + 10)',
    title: isWindow ? 'Window metallic: film width − gusset size' : 'Full metallic: film width + 10 mm',
  };
}

/** "BOPP SS 5.2 g/m · lam coat 12.0 g/m in bag, buy at 12.0 g/m" */
export function filmNote(s: Sku, r: CalcResult): string {
  const bits: string[] = [];
  if (r.hasBopp) bits.push('BOPP ' + s.boppVariant + ' ' + fmt(r.boppWpm, 1) + ' g/m');
  if (r.metWpm > 0) bits.push('metallic ' + fmt(r.metWpm, 1) + ' g/m');
  if (r.hasLam) bits.push('lam coat ' + fmt(r.lamWpm, 1) + ' g/m in bag, buy at ' + fmt(r.coatBuyGpm, 1) + ' g/m');
  return bits.join(' · ');
}

// ---------- print / ink ----------

export const inkHeading = (t: BagType): string =>
  t === 'bopp' ? 'Ink colours — BOPP (rates from BOPP section)' : 'Ink colours — Flexo (rates from Printing section)';

export interface InkOption {
  id: string;
  name: string;
  checked: boolean;
}

/** Ink tick-boxes: BOPP bags draw from the BOPP section, others from Printing. */
export function inkOptions(st: Pick<AppState, 'matSections'>, s: Sku): InkOption[] {
  const secId = s.type === 'bopp' ? 'boppSec' : 'print';
  const sec = st.matSections.find((x) => x.id === secId);
  return (sec ? sec.items : [])
    .filter((it) => it.id.indexOf('ink') === 0 || it.id.indexOf('bink') === 0 || it.id === 'binkMatMedium')
    .map((it) => ({ id: it.id, name: it.name.replace('Ink — ', ''), checked: (s.inkSel || []).includes(it.id) }));
}

export function toggleInk(cur: string[] | undefined, id: string): string[] {
  const list = cur || [];
  return list.includes(id) ? list.filter((x) => x !== id) : list.concat([id]);
}

/** Code-number inputs for each selected ink that still exists. */
export function inkCodeRows(st: Pick<AppState, 'matSections'>, s: Sku): { id: string; name: string; code: string }[] {
  return (s.inkSel || [])
    .filter((id) => matGet(st, id))
    .map((id) => ({ id, name: matName(st, id).replace('Ink — ', ''), code: (s.inkCodes || {})[id] || '' }));
}

// ---------- liner / handle / yarn ----------

/** mm per display unit for liner inputs (stored in mm). */
export const linerFactor = (unit: Unit): number => (unit === 'in' ? 25.4 : unit === 'cm' ? 10 : 1);

export function linerShown(vMm: NumInput, unit: Unit): number | '' {
  const n = num(vMm);
  return n ? Math.round((n / linerFactor(unit)) * 100) / 100 : '';
}

export function linerEq(vMm: NumInput, unit: Unit): string {
  const n = num(vMm);
  if (!n) return '';
  return unit === 'mm' ? '= ' + fmt(n / 10, 1) + ' cm' : '= ' + fmt(n, 0) + ' mm';
}

/** Liner grams: 2 × W × L (m) × micron × 0.92, or the fixed gram weight. */
export function linerGrams(s: Sku): string {
  const w = num(s.linerWmm);
  const l = num(s.linerLmm);
  const m = num(s.linerMic);
  return fmt(w > 0 && l > 0 && m > 0 ? 2 * (w / 1000) * (l / 1000) * m * 0.92 : num(s.linerG), 2);
}

export const handleForced = (s: Sku): boolean => s.type === 'box' && s.boxStyle === 'handleLoop';

/** Yarn colours are stored comma-joined in `yarnColor`; up to 4. */
export function yarnColors(s: Sku): string[] {
  const raw = s.yarnColor || '';
  return raw === '' ? [''] : raw.split(',').map((x) => x.trim());
}

export const joinYarn = (a: string[]): string => (a.length ? a : ['']).join(', ');

// ---------- order & QC ----------

export function qtyKg(s: Sku, r: CalcResult): number | '' {
  return num(s.qty) > 0 && r.totalG > 0 ? Math.round((num(s.qty) * r.totalG) / 1000) : '';
}

/** Predicted tape strength vs the fill capacity's need (≥ 5 gpd for 25 kg+). */
export function fillStrength(s: Sku, r: CalcResult, M: QcModel): { text: string; tone: TextTone } {
  const fill = num(s.fillKg);
  if (!(fill > 0) || !(r.machD > 0)) return { text: '', tone: 'subtle' };
  const gpd = M.s0 + M.s1 * mixPct(s.tapeMix).cc;
  const need = fill >= 25 ? 5 : fill >= 10 ? 4.5 : 4;
  const ok = gpd >= need;
  return {
    text:
      fmt(gpd, 1) + ' gpd · ' + fmt((gpd * r.machD) / 1000, 2) + ' kg/tape — ' +
      (ok ? 'OK for ' + fmt(fill, 0) + ' kg' : 'low for ' + fmt(fill, 0) + ' kg (needs ≥ ' + need + ' gpd)'),
    tone: ok ? 'ok' : 'warn',
  };
}

export function qcStrPred(s: Sku, r: CalcResult, M: QcModel): string {
  const gpd = M.s0 + M.s1 * mixPct(s.tapeMix).cc;
  if (!(r.machD > 0)) return 'Predicted: ' + fmt(gpd, 1) + ' gpd';
  return 'Predicted: ' + fmt(gpd, 1) + ' gpd · ' + fmt((gpd * r.machD) / 1000, 2) + ' kg/tape';
}

export function qcElPred(s: Sku, M: QcModel): string {
  return 'Predicted: ' + fmt(M.e0 + M.e1 * mixPct(s.tapeMix).cc, 1) + ' %';
}

export function qcModelNote(M: QcModel): string {
  return (
    'Model: 38 baseline tests' + (M.nLog > 0 ? ' + ' + M.nLog + ' logged' : '') +
    ' · gpd = ' + fmt(M.s0, 2) + ' − ' + fmt(-M.s1, 3) + '×CC% · elong = ' + fmt(M.e0, 1) + ' − ' + fmt(-M.e1, 3) + '×CC%' +
    (M.nLog >= 5 ? ' (refit from your logged tests)' : M.nLog > 0 ? ' (refits from 5 logged tests)' : '')
  );
}

export interface BlendShares {
  pp: string;
  cc: string;
  mb: string;
  lldpe: string;
  other: string;
  /** CC above 30% is local-grade filler */
  ccTone: 'ok' | 'warn';
}

export function blendShares(mix: MixRow[] | undefined): BlendShares {
  const p = mixPct(mix);
  return {
    pp: fmt(p.pp, 1) + '%',
    cc: fmt(p.cc, 1) + '%',
    mb: fmt(p.mb, 1) + '%',
    lldpe: fmt(p.lldpe, 1) + '%',
    other: p.other > 0.05 ? 'Other ' + fmt(p.other, 1) + '%' : '',
    ccTone: p.cc > 30 ? 'warn' : 'ok',
  };
}

/** A measured test record for the QC model (strength/elongation only when entered). */
export function qcRecord(s: Sku, r: CalcResult): QcLogEntry {
  const cc = mixPct(s.tapeMix).cc;
  const rec: QcLogEntry = {
    t: Date.now(),
    job: s.jobId || '',
    cc: Math.round(cc * 100) / 100,
    den: r.machD > 0 ? Math.round(r.machD) : null,
  };
  if (num(s.strength) > 0) rec.gpd = num(s.strength);
  if (num(s.elong) > 0) rec.el = num(s.elong);
  return rec;
}

// ---------- material mix ----------

export function ashHint(s: Sku): string {
  const a = num(s.ashPct);
  return 'Ash ' + fmt(a, 1) + '% → ~' + fmt(a / 0.78, 1) + '% CC in tape blend (78% ash yield)';
}

/** Tape mix with CC sized so CC% ≈ ash% ÷ 0.78; null when it can't be applied. */
export function ashMix(s: Sku): MixRow[] | null {
  const p = num(s.ashPct) / 0.78 / 100;
  if (!(p > 0 && p < 1)) return null;
  let mix = (s.tapeMix || []).slice();
  if (!mix.find((x) => x.m === 'cc')) mix.push({ m: 'cc', wt: 0 });
  const others = mix.filter((x) => x.m !== 'cc').reduce((a, x) => a + num(x.wt), 0);
  if (others <= 0) return null;
  const ccWt = Math.round(((others * p) / (1 - p)) * 100) / 100;
  mix = mix.map((x) => (x.m === 'cc' ? Object.assign({}, x, { wt: ccWt }) : x));
  return mix;
}

export interface MixRowView {
  m: string;
  wt: NumInput;
  /** share of the blend, "—" for blank rows */
  pct: string;
}

export function mixRowsView(
  st: Pick<AppState, 'matSections'>,
  mix: MixRow[] | undefined,
  secId: string,
): { opts: { id: string; name: string }[]; rows: MixRowView[] } {
  const sec = st.matSections.find((x) => x.id === secId);
  const list = mix || [];
  const tot = list.reduce((a, r) => a + Math.max(0, num(r.wt)), 0);
  return {
    opts: (sec ? sec.items : []).map((it) => ({ id: it.id, name: it.name })),
    rows: list.map((r) => ({
      m: r.m,
      wt: r.wt,
      pct: tot > 0 && num(r.wt) > 0 ? fmt((num(r.wt) / tot) * 100, 1) + '%' : '—',
    })),
  };
}
