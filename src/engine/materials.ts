import { num, todayISO } from './format';
import type { AppState, MaterialItem, MixRow, RateHistoryEntry } from './types';

type RateCtx = Pick<AppState, 'matSections' | 'rateHistory'>;

export function matGet(st: Pick<AppState, 'matSections'>, id: string): MaterialItem | null {
  for (const sec of st.matSections) {
    const it = sec.items.find((x) => x.id === id);
    if (it) return it;
  }
  return null;
}

export function matName(st: Pick<AppState, 'matSections'>, id: string): string {
  const it = matGet(st, id);
  return it ? it.name : id;
}

export interface EffectiveRate {
  /** effective date */
  e: string;
  r: number;
}

const effCache = new WeakMap<RateHistoryEntry[], { today: string; map: Record<string, EffectiveRate> }>();

/**
 * Latest rate per material whose effective date is on or before today.
 * Future-dated entries are scheduled and ignored until their date arrives.
 */
export function effMap(st: Pick<AppState, 'rateHistory'>, today: string = todayISO()): Record<string, EffectiveRate> {
  const hist = st.rateHistory || [];
  const hit = effCache.get(hist);
  if (hit && hit.today === today) return hit.map;
  const map: Record<string, EffectiveRate> = {};
  hist.forEach((h) => {
    const e = h.eff || h.d;
    if (e <= today) {
      const c = map[h.m];
      if (!c || e >= c.e) map[h.m] = { e, r: num(h.r) };
    }
  });
  effCache.set(hist, { today, map });
  return map;
}

/** Current rate (₹/kg): effective history entry first, else the material's list rate. */
export function matRate(st: RateCtx, id: string): number {
  const m = effMap(st)[id];
  if (m) return m.r;
  const it = matGet(st, id);
  return it ? num(it.rate) : 0;
}

export interface MixPct {
  pp: number;
  cc: number;
  mb: number;
  lldpe: number;
  other: number;
}

/** Percentage composition of a blend by weight. */
export function mixPct(mix: MixRow[] | undefined): MixPct {
  let tot = 0;
  const by: Record<string, number> = {};
  (mix || []).forEach((r) => {
    const w = num(r.wt);
    if (w > 0) {
      tot += w;
      by[r.m] = (by[r.m] || 0) + w;
    }
  });
  const pct = (id: string) => (tot > 0 ? ((by[id] || 0) / tot) * 100 : 0);
  const known = pct('pp') + pct('cc') + pct('colmb') + pct('lldpe') + pct('ldpe040') + pct('ldpe1070');
  return {
    pp: pct('pp'),
    cc: pct('cc'),
    mb: pct('colmb'),
    lldpe: pct('lldpe') + pct('ldpe040') + pct('ldpe1070'),
    other: tot > 0 ? Math.max(0, 100 - known) : 0,
  };
}

/** Weighted-average ₹/kg of a blend. */
export function mixBlend(st: RateCtx, mix: MixRow[] | undefined): number {
  let tw = 0;
  let tc = 0;
  (mix || []).forEach((r) => {
    const w = num(r.wt);
    if (w > 0) {
      tw += w;
      tc += w * matRate(st, r.m);
    }
  });
  return tw > 0 ? tc / tw : 0;
}

/** Monday (local time) of the week containing `ds`, as YYYY-MM-DD. */
export function weekStart(ds: string): string {
  const d = new Date(ds + 'T00:00:00');
  const off = (d.getDay() + 6) % 7;
  d.setDate(d.getDate() - off);
  return d.toISOString().slice(0, 10);
}

/** One point per week: the latest-effective rate recorded in that week. */
export function weeklySeries(st: Pick<AppState, 'rateHistory'>, matId: string): { week: string; rate: number }[] {
  const buckets: Record<string, { rate: number; eff: string }> = {};
  (st.rateHistory || [])
    .filter((h) => h.m === matId)
    .forEach((h) => {
      const e = h.eff || h.d;
      const w = weekStart(e);
      const c = buckets[w];
      if (!c || e >= c.eff) buckets[w] = { rate: num(h.r), eff: e };
    });
  return Object.keys(buckets)
    .sort()
    .map((w) => ({ week: w, rate: buckets[w].rate }));
}

export const RATE_HISTORY_CAP = 20000;

/** Replace any same-day entry for the material, then append; caps history length. */
export function withRateLogged(
  hist: RateHistoryEntry[],
  itemId: string,
  rate: number,
  eff: string,
  today: string = todayISO(),
): RateHistoryEntry[] {
  let next = (hist || []).filter((h) => !(h.m === itemId && (h.eff || h.d) === eff));
  next = next.concat([{ m: itemId, d: today, eff, r: rate }]);
  if (next.length > RATE_HISTORY_CAP) next = next.slice(next.length - RATE_HISTORY_CAP);
  return next;
}
