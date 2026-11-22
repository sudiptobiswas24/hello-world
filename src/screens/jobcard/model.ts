/**
 * View-model for the Job card ("Bag study report") — a straight port of the
 * design's "job card" section of renderVals().
 */
import { TYPE_CONFIG, fmt, num, wantsHandle } from '../../engine';
import type { CalcResult, Sku } from '../../engine';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "05 Mar 2026" */
export function dateLabel(d: Date): string {
  return d.getDate().toString().padStart(2, '0') + ' ' + MONTHS[d.getMonth()] + ' ' + d.getFullYear();
}

export interface JobField {
  label: string;
  /** key in `sku.job`; null for the read-only value taken from the calculator */
  key: string | null;
  val: string;
}

export interface JobSection {
  title: string;
  fields: JobField[];
}

export interface JobCardView {
  jobIdS: string;
  dateS: string;
  name: string;
  party: string;
  typeLabel: string;
  qtyS: string;
  qtyKgS: string;
  /** bag spec lines, in display order: [label, value] */
  spec: [string, string][];
  sections: JobSection[];
}

const LOOK_NAMES: Record<string, string> = { milky: 'Milky white', natural: 'Natural' };
const WIN_NAMES: Record<string, string> = { none: '—', offcentre: 'Off-centre window', middle: 'Middle window' };
const PAT_NAMES: Record<string, string> = { none: '—', checker: 'Checker', sideBorder: 'Side border', rangoli: 'Rangoli' };

export function jobCardView(s: Sku, r: CalcResult, today: Date): JobCardView {
  const jb = s.job || {};
  const F = (key: string, label: string): JobField => ({ label, key, val: jb[key] == null ? '' : String(jb[key]) });

  const filmBits: string[] = [];
  if (s.lamSide !== 'none') filmBits.push('Lam ' + s.lamSide);
  if (s.boppVariant !== 'none') filmBits.push(s.boppVariant + ' BOPP ' + s.boppMic + 'µ ' + s.finish);
  if (s.metallic !== 'none') filmBits.push(s.metallic + ' metallic ' + s.metMic + 'µ');
  if (s.linerOn) filmBits.push('liner ' + s.linerG + ' g');
  if (wantsHandle(s)) filmBits.push('handle ' + (s.handleWt || 2) + ' g' + (s.handleColor ? ' ' + s.handleColor : ''));

  const gusset = num(s.gusset);
  const spec: [string, string][] = [
    ['Size', fmt(r.W * 25.4, 0) + ' × ' + fmt(r.L * 25.4, 0) + ' mm (' + fmt(r.W, 1) + ' × ' + fmt(r.L, 1) + '″)'],
    ['Cut length', fmt(r.cutIn, 2) + '″ / ' + fmt(r.cutIn * 25.4, 0) + ' mm'],
    ['Mesh', s.warpMesh + ' × ' + s.weftMesh],
    ['GSM', fmt(r.gsm, 1)],
    ['Machine denier', fmt(r.warpD, 0)],
    ['Unlam denier', fmt(r.machD, 0) + ' (−' + s.shrink + '%)'],
    ['Bag weight', fmt(r.totalG, 1) + ' g'],
    ['Fold', s.foldType + ' ' + fmt(num(s.fold), 2) + '″' + (s.hemOn ? ' + hem 1″' : '')],
    ['Fabric', (LOOK_NAMES[s.fabricType] || 'Milky white') + (s.fabricType === 'milky' && s.window !== 'none' ? ' · ' + WIN_NAMES[s.window] : '')],
    ['Pattern', PAT_NAMES[s.pattern] || '—'],
    ['Colour / MB', (s.fabricColor || '—') + (s.mbCode ? ' · MB ' + s.mbCode : '')],
    ['Leno stitch', s.type === 'leno' ? (s.lenoStitch === 'corner' ? 'Corner stitch' : 'Without corner stitch') : '—'],
    [
      'Gusset',
      s.gussetSide && s.gussetSide !== 'none' && gusset > 0
        ? fmt(gusset * (s.gussetSide === 'both' ? 2 : 1), 1) + ' ' + s.unit + ' (' + s.gussetSide + ' side)'
        : '—',
    ],
    ['Ash %', num(s.ashPct) > 0 ? fmt(num(s.ashPct), 1) + ' %' : '—'],
    ['Yarn', (s.yarnColor || '—') + ' · ' + (num(s.yarnDenier) > 0 ? fmt(num(s.yarnDenier), 0) : '1000') + ' D'],
    ['Films', filmBits.length ? filmBits.join(', ') : 'None'],
    ['Strength / elong.', (s.strength || '—') + ' / ' + (s.elong || '—')],
    ['Fabric qty', fmt(r.fabricM, 0) + ' m · ' + fmt(r.fabricKg, 0) + ' kg'],
    ['Bales', r.bales > 0 ? fmt(r.bales, 0) + ' × ' + fmt(num(s.bagsPerBale), 0) : '—'],
  ];

  const sections: JobSection[] = [
    {
      title: 'Tape plant',
      fields: [
        { label: 'Denier (from calc)', key: null, val: fmt(r.machD, 0) },
        F('tapeWidth', 'Tape width mm'),
        F('tapeStn', 'Strength of tape'),
        F('tapeElong', 'Elongation'),
        F('tapeColors', 'Colours'),
      ],
    },
    { title: 'Mixing details', fields: [F('mixPP', 'PP'), F('mixCC', 'CC (filler)'), F('mixTPT', 'TPT'), F('mixOmega', 'Omega'), F('mixMB', 'Colour MB'), F('mixOther', 'Others')] },
  ];
  if (r.hasLam || r.hasBopp) {
    sections.push({
      title: 'Lamination section — materials this job',
      fields: [F('lamPP', 'Lam PP'), F('lamCC', 'Lam CC'), F('lamTPT', 'TPT'), F('ldpe040', 'LDPE-040'), F('ldpe1070', 'LDPE-1070'), F('lamEtc', 'Etc.')],
    });
  }
  if (r.printed) {
    sections.push({
      title: 'Flexo print',
      fields: [F('drumSize', 'Drum size'), F('gearTeeth', 'Cutting gear teeth no.'), F('printArea', 'Printing area size'), F('drums', 'No. of drums'), F('inks', 'Flexo inks required')],
    });
  }
  sections.push(
    { title: 'BCS machine', fields: [F('cutType', 'Plain cut / zigzag cut'), F('threadColor', 'Thread colour / DNR'), F('bcs24', 'Production in 24 hours')] },
    { title: 'Finishing section', fields: [F('finDays', 'Days to finish job'), F('finThread', 'Thread: TP tape / PP yarn'), F('finColor', 'Colour of yarn / TP tape'), F('finPerDay', 'Per-day finish production')] },
    { title: 'Baling section', fields: [F('balesPerDay', 'Bales per day'), F('baleQty', 'Per bale qty'), F('baleNotes', 'Individually / notes')] },
    { title: 'Ready for dispatch', fields: [F('dispDate', 'Date'), F('dispQty', 'Quantity'), F('dispEtc', 'Etc.')] },
  );

  return {
    jobIdS: s.jobId || 'JOB —',
    dateS: dateLabel(today),
    name: s.name,
    party: s.party || '—',
    typeLabel: (TYPE_CONFIG[s.type] || TYPE_CONFIG.plain).label,
    qtyS: fmt(r.qty, 0),
    qtyKgS: fmt((r.qty * r.totalG) / 1000, 0),
    spec,
    sections,
  };
}
