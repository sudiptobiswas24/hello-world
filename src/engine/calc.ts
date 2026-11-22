import { TYPE_CONFIG } from './defaults';
import { fmt, num, toIn } from './format';
import { matGet, matName, matRate, mixBlend } from './materials';
import type { AppState, ConversionRates, Sku, TypeConfig } from './types';

export interface BomLine {
  name: string;
  /** grams per bag */
  g: number;
  /** kg for the whole order */
  kg: number;
  /** ₹ / kg */
  rate: number;
  /** ₹ per bag */
  cost: number;
}

export interface ConvLine {
  name: string;
  basis: string;
  /** ₹ per bag */
  cost: number;
}

export interface Issue {
  level: 'error' | 'warn';
  msg: string;
}

export interface CalcResult {
  cfg: TypeConfig;
  /** width, inches */
  W: number;
  /** length, inches */
  L: number;
  /** gusset, inches (job-card info only) */
  Gz: number;
  wEff: number;
  cutIn: number;
  cutM: number;
  gsm: number;
  warpD: number;
  weftD: number;
  machD: number;
  gpmTotal: number;
  gpmLam: number;
  gpmUnlam: number;
  fabGram: number;
  gsmLam: number;
  lamD: number;
  lamEntered: boolean;
  net: number;
  totalG: number;
  totalKg: number;
  hasLam: boolean;
  hasBopp: boolean;
  boppWpm: number;
  metWpm: number;
  lamWpm: number;
  coatBuyGpm: number;
  printed: boolean;
  fabricM: number;
  lamM: number;
  boppM: number;
  fabricKg: number;
  lamKg: number;
  boppKg: number;
  qty: number;
  bom: BomLine[];
  conv: ConvLine[];
  matCost: number;
  convCost: number;
  ovh: number;
  costBag: number;
  costKg: number;
  costM: number;
  marginPct: number;
  marginAmt: number;
  quote: number;
  orderVal: number;
  gstAmt: number;
  grand: number;
  allKgOrder: number;
  issues: Issue[];
  errCount: number;
  bales: number;
}

export type CalcState = Pick<AppState, 'glob' | 'wast' | 'convs' | 'matSections' | 'rateHistory' | 'skus'>;

const CONV_NAMES: Record<keyof ConversionRates, string> = {
  tape: 'Tape extrusion', weaving: 'Weaving', lamination: 'Lamination', printing: 'Printing',
  cutting: 'Cutting + stitching', linerins: 'Liner insertion', dcut: 'D-cut punching',
  handleAt: 'Handle attachment', valveAt: 'Valve patch fixing', packing: 'Bale packing',
};

/** Does this spec carry a handle (explicit, or a box bag in handle-loop style)? */
export function wantsHandle(s: Sku): boolean {
  return s.type !== 'leno' && (s.handleOn || (s.type === 'box' && s.boxStyle === 'handleLoop'));
}

/**
 * The bag study chain: cut length → meter weight → GSM → denier, order
 * consumption, bill of materials, conversion, quote, and validation.
 * Units: dimensions converted to inches; 1 m = 39.37 in.
 */
export function calc(st: CalcState, s: Sku): CalcResult {
  const N = num;
  const g = st.glob;
  const wa = st.wast;
  const cfg = TYPE_CONFIG[s.type] || TYPE_CONFIG.plain;
  const W = Math.max(0, toIn(N(s.width), s.unit));
  const L = Math.max(0, toIn(N(s.length), s.unit));
  const Gz = s.gussetSide && s.gussetSide !== 'none' ? toIn(N(s.gusset), s.unit) : 0;
  const hem = s.hemOn ? 1 : 0; // hem adds a fixed 1″ (25.4 mm)
  const cutIn = L + N(s.fold) + hem;
  const cutM = cutIn / 39.37;
  const wEff = W; // gusset is not included in width math
  const wm = N(s.warpMesh) || 10;
  const fm = N(s.weftMesh) || 10;
  const qty = Math.max(1, N(s.qty));

  // film weights g/m: width mm × micron ÷ 1000; ×2 only for both-side (BS)
  const hasBopp = s.boppVariant !== 'none';
  const boppWpm = hasBopp ? (N(s.boppW) * N(s.boppMic)) / 1000 * (s.boppVariant === 'BS' ? 2 : 1) : 0;
  const metWpm = s.metallic !== 'none' ? (N(s.metW) * N(s.metMic)) / 1000 * (s.metallic === 'window' ? 0.5 : 1) : 0;

  // lamination coating: user-entered GPM (g/m), used as-is
  const hasLam = s.lamSide !== 'none';
  let lamWpm = 0;
  let lamEntered = false;
  if (hasLam) {
    const lv = N(s.lamVal);
    if (lv > 0) {
      lamEntered = true;
      lamWpm = lv;
    }
  }
  const coatBuyGpm = lamWpm;

  // thread grams per stitch row: seam length × (stitch density ÷ 12.5 ref) × chain-stitch factor × denier ÷ 9000
  const stDm = N(g.stitchDm) || 12.5;
  const thFac = N(g.threadFactor) || 4.5;
  const yDen = N(s.yarnDenier) || 1000;
  const thRow = (W * 0.0254 * (stDm / 12.5) * thFac * yDen) / 9000;
  const hemG = s.hemOn ? thRow : 0;
  const foldG = N(s.fold) > 0 ? thRow * ((s.foldType || '').indexOf('DS') >= 0 ? 2 : 1) : 0;

  let linerG = 0;
  if (s.linerOn) {
    const lw = N(s.linerWmm);
    const ll = N(s.linerLmm);
    const lm = N(s.linerMic);
    linerG = lw > 0 && ll > 0 && lm > 0 ? 2 * (lw / 1000) * (ll / 1000) * lm * 0.92 : N(s.linerG);
  }
  const handle = wantsHandle(s);
  const handleG = handle ? N(s.handleWt) || 2 : 0;

  let gsm: number;
  let warpD: number;
  let weftD: number;
  let gpmUnlam: number;
  let gpmLam: number;
  let gpmTotal: number;
  let net: number;
  let totalG: number;
  if (s.mode === 'wt') {
    totalG = N(s.targetWt);
    net = Math.max(0, totalG - handleG - linerG - hemG - foldG);
    gpmTotal = cutIn > 0 ? (net / cutIn) * 39.37 : 0; // fabric meter weight (lam + BOPP)
    gpmLam = gpmTotal - boppWpm - metWpm;
    gpmUnlam = gpmLam - lamWpm;
    gsm = wEff > 0 ? (gpmUnlam * 39.37) / (2 * wEff) : 0;
    warpD = weftD = (gsm * 9000) / ((wm + fm) * 39.37);
  } else {
    warpD = N(s.warpD);
    weftD = N(s.weftD);
    gsm = ((wm * warpD + fm * weftD) * 39.37) / 9000;
    gpmUnlam = (gsm * 2 * wEff) / 39.37;
    gpmLam = gpmUnlam + lamWpm;
    gpmTotal = gpmLam + boppWpm + metWpm;
    net = (gpmTotal * cutIn) / 39.37;
    totalG = net + handleG + linerG + hemG + foldG;
  }
  const fabGram = wEff > 0 ? gpmTotal / wEff : 0; // trade "gram" quality number
  const gsmLam = wEff > 0 ? (gpmLam * 39.37) / (2 * wEff) : 0;
  const lamD = (gsmLam * 9000) / ((wm + fm) * 39.37);
  const machD = warpD * (1 - N(s.shrink) / 100);

  // order quantities: allowances, rounded up to the increment
  const baseM = (qty * cutIn) / 39.37;
  const inc = N(g.roundM) > 0 ? N(g.roundM) : 100;
  const roundUp = (x: number) => Math.ceil(x / inc) * inc;
  const fabricM = roundUp(baseM * (1 + N(wa.fabAllow) / 100));
  const lamM = hasLam ? roundUp(baseM * (1 + N(wa.lamAllow) / 100)) : 0;
  const boppM = hasBopp || metWpm > 0 ? roundUp(baseM * (1 + N(wa.boppAllow) / 100)) : 0;
  const fabricKg = (fabricM * gpmUnlam) / 1000;
  const lamKg = (lamM * coatBuyGpm) / 1000;
  const boppOnlyKg = (boppM * boppWpm) / 1000;
  const metKg = (boppM * metWpm) / 1000;
  const boppKg = boppOnlyKg + metKg;
  const printed = s.printed === 'yes';
  const totColors = printed ? N(s.colors) + (s.printSides === 'both' ? N(s.colorsBack) : 0) : 0;
  const coverage = N(g.inkCoverage) > 0 ? N(g.inkCoverage) / 100 : 1;
  const inkKg = printed ? (totColors * N(g.inkFactor) * coverage * (cutM * wEff * 0.0254) * qty) / 1000 : 0;
  const threadKg = ((hemG + foldG) * qty) / 1000;
  const linerKg = (linerG * qty) / 1000;
  const handleKg = (handleG * qty) / 1000;

  // BOM — rates from sectioned materials; blends from per-bag mixes
  const blend = mixBlend(st, s.tapeMix);
  const lamBlend = mixBlend(st, s.lamMix);
  const inkSel = (s.inkSel || []).filter((id) => matGet(st, id));
  const inkRate = inkSel.length ? inkSel.reduce((a, id) => a + matRate(st, id), 0) / inkSel.length : 0;
  const redKg = (inkKg * N(g.redPct)) / 100;
  const mibkKg = (inkKg * N(g.mibkPct)) / 100;
  const bom: BomLine[] = [];
  const push = (name: string, kgOrder: number, rate: number) => {
    if (kgOrder > 0.0001) bom.push({ name, g: (kgOrder * 1000) / qty, kg: kgOrder, rate, cost: (kgOrder * rate) / qty });
  };
  const mixDesc = (mix: Sku['tapeMix']) =>
    (mix || []).filter((r) => num(r.wt) > 0).map((r) => matName(st, r.m)).join(' + ') || 'blend';
  push('Tape blend (' + mixDesc(s.tapeMix) + ')', fabricKg, blend);
  push('Lam coating (' + mixDesc(s.lamMix) + ', ' + fmt(coatBuyGpm, 1) + ' g/m)', lamKg, lamBlend);
  push('BOPP film', boppOnlyKg, matRate(st, 'boppFilm'));
  push('Metallic / window film', metKg, matRate(st, 'metFilm'));
  push('Ink (' + totColors + ' colors: ' + (inkSel.map((id) => matName(st, id).replace('Ink — ', '')).join(', ') || '—') + ')', inkKg, inkRate);
  push('Reducer (' + fmt(N(g.redPct), 0) + '% of ink)', redKg, matRate(st, 'reducer'));
  push('MIBK (' + fmt(N(g.mibkPct), 0) + '% of ink)', mibkKg, matRate(st, 'mibk'));
  push('LDPE liner', linerKg, matRate(st, 'liner'));
  push('Stitching thread / yarn (' + fmt(yDen, 0) + ' D, ' + fmt(stDm, 1) + ' st/dm)', threadKg, matRate(st, 'thread'));
  push('Handle strip', handleKg, matRate(st, 'handle'));
  const matCost = bom.reduce((a, b) => a + b.cost, 0);

  // conversion: per-kg stages on total bag weight, then per-bag extras
  const cv = st.convs;
  const totalKg = totalG / 1000;
  const stages: (keyof ConversionRates)[] = ['tape', 'weaving'];
  if (hasLam || hasBopp) stages.push('lamination');
  if (printed) stages.push('printing');
  stages.push('cutting');
  const conv: ConvLine[] = [];
  stages.forEach((k) => {
    const r = N(cv[k]);
    conv.push({ name: CONV_NAMES[k], basis: '₹ ' + fmt(r) + ' / kg', cost: r * totalKg });
  });
  const extras = (cfg.extras || []).slice();
  if (s.type === 'box' && s.boxStyle === 'dcut') extras.push('dcut');
  if (handle) extras.push('handleAt');
  if (s.linerOn) extras.push('linerins');
  extras.forEach((k) => {
    const r = N(cv[k]);
    conv.push({ name: CONV_NAMES[k], basis: '₹ ' + fmt(r) + ' / bag', cost: r });
  });
  conv.push({ name: CONV_NAMES.packing, basis: '₹ ' + fmt(N(cv.packing)) + ' / bag', cost: N(cv.packing) });
  const convCost = conv.reduce((a, c) => a + c.cost, 0);
  const ovh = ((matCost + convCost) * N(g.overhead)) / 100;
  const costBag = matCost + convCost + ovh;
  const marginPct = s.marginOv === '' || s.marginOv == null ? N(g.margin) : N(s.marginOv);
  const marginAmt = (costBag * marginPct) / 100;
  const quote = costBag + marginAmt;
  const orderVal = quote * qty;
  const gstAmt = (orderVal * N(g.gst)) / 100;

  // validation
  const issues: Issue[] = [];
  const err = (msg: string) => issues.push({ level: 'error', msg });
  const warn = (msg: string) => issues.push({ level: 'warn', msg });
  if (W <= 0) err('Width is required. Enter a value greater than 0.');
  if (L <= 0) err('Length is required. Enter a value greater than 0.');
  if (N(s.qty) < 1) err('Order qty is required. Enter at least 1 bag.');
  if (wm < 3 || wm > 20 || fm < 3 || fm > 20) warn('Mesh ' + wm + '×' + fm + ' is outside the usual 3–20 range.');
  if (s.mode === 'wt' && W > 0 && L > 0 && net <= 0) err('Target weight too low. Add-ons alone weigh more than ' + fmt(N(s.targetWt), 0) + ' g — raise target weight or remove add-ons.');
  if (gsm > 0 && (gsm < 40 || gsm > 200)) warn('Fabric GSM ' + fmt(gsm, 0) + ' is outside the usual 40–200 range.');
  if (gsm <= 0 && W > 0 && L > 0 && N(s.qty) >= 1) err('Fabric GSM computes to 0. Check target weight, films and dimensions.');
  if (warpD > 0 && (warpD < 300 || warpD > 2500)) warn('Denier ' + fmt(warpD, 0) + ' is outside the usual 350–2000 range.');
  if (N(s.shrink) < 0 || N(s.shrink) > 15) warn('Shrink % is usually 2–8. Check the value.');
  if (printed && totColors < 1) err('Print colors is required. Enter at least 1 color, or set the bag to unprinted.');
  if (hasBopp && N(s.boppW) > 0 && N(s.boppW) < wEff * 25.4) warn('BOPP film ' + fmt(N(s.boppW), 0) + ' mm is narrower than bag width ' + fmt(wEff * 25.4, 0) + ' mm.');
  if (hasBopp && N(s.boppW) <= 0) err('Film width mm is required for BOPP. Enter the film width.');
  if (hasBopp && N(s.boppMic) <= 0) err('Micron is required for BOPP. Enter the film micron.');
  if (s.mode === 'wt' && N(s.targetWt) <= 0) err('Target wt g is required. Enter the finished bag weight.');
  if (s.linerOn && linerG <= 0) warn('Liner weight is 0. Enter liner width, length and micron — or a fixed gram weight.');
  if (hasLam && !lamEntered) err('Lamination GPM is required. Enter the coating weight in g/m.');
  if (marginPct < 0) warn('Margin is negative — quote is below cost.');
  if (s.jobId && (st.skus || []).filter((k) => (k.jobId || '') === s.jobId).length > 1) warn('Job ID “' + s.jobId + '” is used by another bag. Make it unique before dispatch.');
  if (blend <= 0) err('Tape mix is empty. Add material weights in the Material mix section.');
  if (hasLam && lamBlend <= 0) err('Lamination mix is empty. Add material weights in the Material mix section.');
  if (printed && inkSel.length === 0) err('No ink colours selected. Tick the colours this bag prints in.');
  if (hasBopp && matRate(st, 'boppFilm') <= 0) err('BOPP film rate missing. Add a current rate on the Rates page.');
  if (metWpm > 0 && matRate(st, 'metFilm') <= 0) err('Metallic film rate missing. Add a current rate on the Rates page.');
  if (s.linerOn && matRate(st, 'liner') <= 0) err('LDPE liner rate missing. Add a current rate on the Rates page.');
  if (threadKg > 0 && matRate(st, 'thread') <= 0) err('Stitching thread rate missing. Add a current rate on the Rates page.');
  const errCount = issues.filter((i) => i.level === 'error').length;

  return {
    cfg, W, L, Gz, wEff, cutIn, cutM, gsm, warpD, weftD, machD,
    gpmTotal, gpmLam, gpmUnlam, fabGram, gsmLam, lamD, lamEntered, net, totalG, totalKg,
    hasLam, hasBopp, boppWpm, metWpm, lamWpm, coatBuyGpm, printed,
    fabricM, lamM, boppM, fabricKg, lamKg, boppKg, qty,
    bom, conv, matCost, convCost, ovh, costBag,
    costKg: totalKg > 0 ? costBag / totalKg : 0,
    costM: cutM > 0 ? costBag / cutM : 0,
    marginPct, marginAmt, quote, orderVal, gstAmt, grand: orderVal + gstAmt,
    allKgOrder: bom.reduce((a, b) => a + b.kg, 0),
    issues, errCount,
    bales: N(s.bagsPerBale) > 0 ? Math.ceil(qty / N(s.bagsPerBale)) : 0,
  };
}
