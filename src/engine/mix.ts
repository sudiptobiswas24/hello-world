import type { CalcResult } from './calc';
import { fmt, inr, num } from './format';
import { matRate, mixBlend, mixPct } from './materials';
import { qcModel } from './qc';
import type { AppState, MixRow, Sku } from './types';

/** Severity colours used for finding dots (match the design's status palette). */
export const FINDING_COLOR = {
  error: '#B42318',
  warn: '#B54708',
  info: '#6F6B5E',
  good: '#027A48',
} as const;

export type FindingTone = keyof typeof FINDING_COLOR;

export interface MixFinding {
  tone: FindingTone;
  msg: string;
  /** label for the one-click fix, when there is one */
  fixLabel?: string;
  /** replacement tape mix the fix applies */
  fixMix?: MixRow[];
}

export interface MixAnalysis {
  /** predicted strength, gpd */
  gpd: number;
  /** predicted elongation, % */
  el: number;
  grade: 'standard' | 'local' | 'none';
  mixGrade: string;
  mixStats: string;
  findings: MixFinding[];
  hasRec: boolean;
  recMix: MixRow[];
  recDesc: string;
}

type MixState = Pick<AppState, 'matSections' | 'rateHistory' | 'qcLog'>;

const PLAIN_WHITE = ['', 'white', 'milky', 'milky white', 'natural', 'off white', 'off-white'];
const r2 = (x: number) => Math.round(x * 100) / 100;

/**
 * Checks a spec's tape blend against the QC model: master-batch use vs fabric
 * colour, CC filler vs the strength target, and filler headroom (cost savings).
 */
export function mixAnalysis(st: MixState, s: Sku, r: CalcResult | null): MixAnalysis {
  const N = num;
  const mix = s.tapeMix || [];
  const p = mixPct(mix);
  const tot = mix.reduce((a, x) => a + N(x.wt), 0);
  const M = qcModel(st);
  const gpd = M.s0 + M.s1 * p.cc;
  const el = M.e0 + M.e1 * p.cc;
  const blend = mixBlend(st, mix);
  const col = (s.fabricColor || '').trim().toLowerCase();
  const isNat = s.fabricType === 'natural';
  const plainWhite = PLAIN_WHITE.indexOf(col) >= 0;
  const noMb = isNat || plainWhite;
  const target = N(s.strength) > 0 ? N(s.strength) : 5;
  const oth = mix.filter((x) => x.m !== 'colmb').reduce((a, x) => a + N(x.wt), 0);
  const othNoCC = mix.filter((x) => x.m !== 'cc').reduce((a, x) => a + N(x.wt), 0);
  const maxCC = (M.s0 - target) / -M.s1;
  const rPP = matRate(st, 'pp');
  const rCC = matRate(st, 'cc');
  const rMB = matRate(st, 'colmb');
  const blendOf = (cc: number, mb: number) => {
    const pp = Math.max(0, 100 - cc - mb);
    return (pp * rPP + cc * rCC + mb * rMB) / 100;
  };
  const fabGBag = r && r.fabricKg > 0 && r.qty > 0 ? (r.fabricKg / r.qty) * 1000 : r ? r.gpmUnlam * r.cutM : 0;
  const capCC = Math.max(0, Math.min(45, Math.floor(maxCC - 1)));
  const mbKeep = noMb ? 0 : p.mb > 0.05 ? Math.round(p.mb * 10) / 10 : 2;
  const look = isNat ? 'natural' : plainWhite ? 'white/milky' : 'coloured';

  const findings: MixFinding[] = [];
  if (tot > 0) {
    if (noMb && p.mb > 0.05) {
      findings.push({ tone: 'error', msg: (isNat ? 'Natural fabric' : 'White / milky fabric') + ' — colour master batch is not needed (mix has ' + fmt(p.mb, 1) + '% MB).', fixLabel: 'Remove MB', fixMix: mix.filter((x) => x.m !== 'colmb') });
    }
    if (!noMb && p.mb < 0.05) {
      findings.push({ tone: 'warn', msg: 'Coloured fabric “' + s.fabricColor + '” but no colour MB in the mix — typical dose 2%.', fixLabel: 'Add 2% MB', fixMix: mix.concat([{ m: 'colmb', wt: r2((oth * 2) / 98) }]) });
    }
    if (!noMb && p.mb > 4) {
      findings.push({ tone: 'warn', msg: 'Colour MB at ' + fmt(p.mb, 1) + '% — above the usual 2–4% dosing; check shade card.' });
    }
    if (p.cc > maxCC + 0.5) {
      const canFix = maxCC > 0 && maxCC < 100;
      const ccWt = r2((othNoCC * (maxCC / 100)) / (1 - maxCC / 100));
      findings.push({
        tone: 'error',
        msg: 'CC ' + fmt(p.cc, 1) + '% predicts ' + fmt(gpd, 2) + ' gpd — below the ' + fmt(target, 1) + ' gpd target. Keep CC ≤ ' + fmt(maxCC, 0) + '%.',
        ...(canFix ? { fixLabel: 'Cap CC at ' + fmt(maxCC, 0) + '%', fixMix: mix.filter((x) => x.m !== 'cc').concat([{ m: 'cc', wt: ccWt }]) } : {}),
      });
    }
    if (isNat && p.cc > 4) {
      findings.push({ tone: 'warn', msg: 'Natural look with ' + fmt(p.cc, 1) + '% CC — above ~4% the fabric turns chalky and loses the natural finish.' });
    }
    if (p.cc > 30 && p.cc <= maxCC + 0.5) {
      findings.push({ tone: 'warn', msg: 'High filler: elongation drops to ~' + fmt(el, 1) + '% — local-grade fabric, expect brittleness in cold weather.' });
    }
    if (p.lldpe > 0.05) {
      findings.push({ tone: 'info', msg: 'LDPE ' + fmt(p.lldpe, 1) + '% in tape mix — normally a lamination material; confirm it is intentional.' });
    }
    if (r && r.machD > 0 && (r.machD < 300 || r.machD > 2000)) {
      findings.push({ tone: 'warn', msg: 'Machine denier ' + fmt(r.machD, 0) + ' is outside the usual 350–2000 range — recheck bag weight, width or mesh.' });
    }
    if (gpd >= target - 0.05 && capCC > p.cc + 1.5) {
      const saveKg = blendOf(p.cc, p.mb) - blendOf(capCC, mbKeep);
      if (saveKg > 0.05) {
        const perBag = (saveKg * fabGBag) / 1000;
        const perOrd = perBag * N(s.qty);
        const optMix: MixRow[] = [{ m: 'pp', wt: r2(100 - capCC - mbKeep) }, { m: 'cc', wt: capCC }].concat(mbKeep > 0 ? [{ m: 'colmb', wt: mbKeep }] : []);
        const saving = fabGBag > 0 ? ' (≈ ' + inr(perBag, 2) + '/bag' + (perOrd > 0 ? ', ' + inr(perOrd, 0) + '/order' : '') + ')' : '';
        findings.push({ tone: 'good', msg: 'Filler headroom: CC can rise to ' + fmt(capCC, 0) + '% and still hold ' + fmt(target, 1) + ' gpd — saves ' + inr(saveKg, 2) + '/kg' + saving + '.', fixLabel: 'Max filler', fixMix: optMix });
      }
    }
  } else {
    findings.push({ tone: 'warn', msg: 'Enter the tape mix weights to analyse the blend.' });
  }
  if (tot > 0 && !findings.length) {
    findings.push({ tone: 'good', msg: 'Mix is consistent: ' + (gpd >= 5 ? 'standard' : 'local') + '-grade ' + (isNat ? 'natural' : plainWhite ? 'milky/white' : 'coloured') + ' fabric, no changes needed.' });
  }

  const ccRec = isNat ? 3 : Math.max(0, Math.min(45, Math.floor(maxCC - 2)));
  const mbRec = noMb ? 0 : 2;
  const ppRec = r2(100 - ccRec - mbRec);
  const recMix: MixRow[] = [{ m: 'pp', wt: ppRec } as MixRow]
    .concat(ccRec > 0 ? [{ m: 'cc', wt: ccRec }] : [])
    .concat(mbRec > 0 ? [{ m: 'colmb', wt: mbRec }] : []);
  const recGpd = M.s0 + M.s1 * ccRec;
  const hasRec = tot > 0 && (Math.abs(p.cc - ccRec) > 1.5 || Math.abs(p.mb - mbRec) > 0.5);
  const confN = 38 + (M.nLog || 0);
  const recBlend = mixBlend(st, recMix);
  const recSave = tot > 0 ? blend - recBlend : 0;
  const grade = tot > 0 ? (gpd >= 5 ? 'standard' : 'local') : 'none';

  return {
    gpd,
    el,
    grade,
    mixGrade: grade === 'none' ? 'No mix' : grade === 'standard' ? 'Standard · ≥ 5 gpd' : 'Local · ' + fmt(gpd, 1) + ' gpd',
    mixStats: tot > 0 ? 'Predicted ' + fmt(gpd, 2) + ' gpd · ' + fmt(el, 1) + '% elong · ' + inr(blend) + ' / kg · n=' + confN : '',
    findings,
    hasRec,
    recMix,
    recDesc:
      'Recommended for ' + fmt(target, 1) + ' gpd ' + look + ': PP ' + ppRec + (ccRec > 0 ? ' / CC ' + ccRec : '') + (mbRec > 0 ? ' / MB ' + mbRec : '') +
      ' → ' + fmt(recGpd, 2) + ' gpd · ' + inr(recBlend) + ' / kg' +
      (recSave > 0.05 ? ' · saves ' + inr(recSave, 2) + '/kg' : recSave < -0.05 ? ' · +' + inr(-recSave, 2) + '/kg for strength' : ''),
  };
}
