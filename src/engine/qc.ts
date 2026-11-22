import type { AppState } from './types';

export interface QcModel {
  /** strength intercept, gpd */
  s0: number;
  /** strength slope, gpd per CC% */
  s1: number;
  /** elongation intercept, % */
  e0: number;
  /** elongation slope, % per CC% */
  e1: number;
  /** number of logged QC tests */
  nLog: number;
}

/** Baseline regression from 38 tested bags. */
export const QC_BASELINE = { s0: 5.74, s1: -0.033, e0: 24.8, e1: -0.144, n: 38 } as const;

/** Least-squares line through points; null when x has no spread. */
export function linearFit(pts: [number, number][]): { b0: number; b1: number } | null {
  const n = pts.length;
  const mx = pts.reduce((a, q) => a + q[0], 0) / n;
  const my = pts.reduce((a, q) => a + q[1], 0) / n;
  let sxy = 0;
  let sx = 0;
  pts.forEach((q) => {
    sxy += (q[0] - mx) * (q[1] - my);
    sx += (q[0] - mx) * (q[0] - mx);
  });
  if (!(sx > 0)) return null;
  const b1 = sxy / sx;
  return { b0: my - b1 * mx, b1 };
}

/**
 * Tape strength (gpd) and elongation (%) as linear functions of CC%.
 * Once a measure has 5 logged tests it is refitted and blended with the baseline,
 * weighted by sample count; a refit with a non-negative slope is ignored.
 */
export function qcModel(st: Pick<AppState, 'qcLog'>): QcModel {
  const base = QC_BASELINE;
  const recs = (st.qcLog || []).filter((x) => Number.isFinite(+(x.cc as number)));
  const out: QcModel = { s0: base.s0, s1: base.s1, e0: base.e0, e1: base.e1, nLog: recs.length };
  const sp = recs.filter((x) => +(x.gpd as number) > 0).map((x) => [+(x.cc as number), +(x.gpd as number)] as [number, number]);
  if (sp.length >= 5) {
    const f = linearFit(sp);
    if (f && f.b1 < 0) {
      const w = sp.length / (sp.length + base.n);
      out.s0 = base.s0 * (1 - w) + f.b0 * w;
      out.s1 = base.s1 * (1 - w) + f.b1 * w;
    }
  }
  const ep = recs.filter((x) => +(x.el as number) > 0).map((x) => [+(x.cc as number), +(x.el as number)] as [number, number]);
  if (ep.length >= 5) {
    const f = linearFit(ep);
    if (f && f.b1 < 0) {
      const w = ep.length / (ep.length + base.n);
      out.e0 = base.e0 * (1 - w) + f.b0 * w;
      out.e1 = base.e1 * (1 - w) + f.b1 * w;
    }
  }
  return out;
}
