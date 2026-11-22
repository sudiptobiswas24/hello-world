/**
 * Rates screen view-model — pure helpers that mirror the design's
 * `renderVals()` formulas for the sectioned material rates, the weekly
 * rate trend, the bulk-update panel and the "rates due" banner.
 */
import { effMap, fmt, inr, num, todayISO, weeklySeries } from '../../engine';
import type { AppState, MaterialItem, MaterialSection, NumInput, RateHistoryEntry } from '../../engine';

type RatesCtx = Pick<AppState, 'matSections' | 'rateHistory'>;

// ---------- formatting ----------

/** Last-updated date, e.g. "07 Nov 26" (design `fmtD`). */
export function fmtD(ds: string): string {
  const dd = new Date(ds + 'T00:00:00');
  return dd.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: '2-digit' });
}

/** Week label, e.g. "03 Nov" (design `fmtWeek`). */
export function fmtWeek(w: string): string {
  const d = new Date(w + 'T00:00:00');
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' });
}

/** Percentage change from `prev` to `cur`; null when there is no usable previous value. */
export function dPct(cur: number, prev: number | null | undefined): number | null {
  return prev != null && prev !== 0 ? ((cur - prev) / prev) * 100 : null;
}

/** up = price rose (red), down = price fell (green), neutral = no change / no data. */
export type DeltaTone = 'neutral' | 'up' | 'down';

export function dTone(d: number | null): DeltaTone {
  return d == null ? 'neutral' : d > 0 ? 'up' : d < 0 ? 'down' : 'neutral';
}

export function dTxt(d: number | null): string {
  return d == null ? '—' : (d >= 0 ? '+' : '') + fmt(d, 1) + '%';
}

export interface Delta {
  text: string;
  tone: DeltaTone;
}

const delta = (d: number | null): Delta => ({ text: dTxt(d), tone: dTone(d) });

// ---------- material categories ----------

/** Free-text category stored on a material (not part of the shared MaterialItem type). */
export function matCat(it: MaterialItem): string {
  const c = (it as MaterialItem & { cat?: unknown }).cat;
  return typeof c === 'string' ? c : '';
}

/** Sections with one material's category replaced (design `setMatItem(sec, id, { cat })`). */
export function withMatCat(sections: MaterialSection[], secId: string, itemId: string, cat: string): MaterialSection[] {
  return sections.map((sec) =>
    sec.id !== secId ? sec : Object.assign({}, sec, { items: sec.items.map((it) => (it.id === itemId ? Object.assign({}, it, { cat }) : it)) }),
  );
}

/** Distinct categories in use, for the category input's suggestions. */
export function matCats(sections: MaterialSection[]): string[] {
  const s = new Set<string>();
  sections.forEach((sec) =>
    sec.items.forEach((it) => {
      const c = matCat(it);
      if (c) s.add(c);
    }),
  );
  return [...s];
}

// ---------- sectioned rate tables ----------

/** Border state of a rate input: unsaved change, has a rate, or missing (≤ 0). */
export type RateTone = 'dirty' | 'set' | 'missing';

export interface RateRow {
  id: string;
  name: string;
  cat: string;
  /** what the rate input shows: the draft if any, else the current rate (blank when 0) */
  rate: NumInput;
  lastUpd: string;
  dirty: boolean;
  tone: RateTone;
}

export interface RateSec {
  id: string;
  label: string;
  items: RateRow[];
}

export function buildRateSecs(st: RatesCtx & Pick<AppState, 'rateDraft'>, today: string = todayISO()): RateSec[] {
  const emap = effMap(st, today);
  const rdraft = st.rateDraft || {};
  return st.matSections.map((sec) => ({
    id: sec.id,
    label: sec.label,
    items: sec.items.map((it): RateRow => {
      const em = emap[it.id];
      const curRate = em ? em.r : num(it.rate);
      const draft = rdraft[it.id];
      const hasDraft = draft != null && draft !== '';
      const shown: NumInput = hasDraft ? (draft as NumInput) : curRate || '';
      const dirty = hasDraft && num(draft) > 0 && num(draft) !== curRate;
      return {
        id: it.id,
        name: it.name,
        cat: matCat(it),
        rate: shown,
        lastUpd: em ? fmtD(em.e) : '—',
        dirty,
        tone: dirty ? 'dirty' : num(shown) > 0 ? 'set' : 'missing',
      };
    }),
  }));
}

// ---------- rate trend ----------

export interface MatOption {
  id: string;
  name: string;
}

/** Every material as "Section · Material". */
export function allMaterials(sections: MaterialSection[]): MatOption[] {
  const out: MatOption[] = [];
  sections.forEach((sec) => sec.items.forEach((it) => out.push({ id: it.id, name: sec.label + ' · ' + it.name })));
  return out;
}

export function histOptions(sections: MaterialSection[]): MatOption[] {
  return [{ id: 'all', name: 'All materials' }].concat(allMaterials(sections));
}

export interface WowRow {
  id: string;
  name: string;
  /** current effective rate */
  rate: string;
  /** latest week with an entry */
  week: string;
  /** latest week vs the week before it */
  delta: Delta;
}

/** "All materials" view: one row per material that has history. */
export function buildWowRows(st: RatesCtx, today: string = todayISO()): WowRow[] {
  const emap = effMap(st, today);
  const out: WowRow[] = [];
  allMaterials(st.matSections).forEach((m) => {
    const ser = weeklySeries(st, m.id);
    if (!ser.length) return;
    const last = ser[ser.length - 1];
    const prev = ser.length > 1 ? ser[ser.length - 2] : null;
    const cur = emap[m.id] ? emap[m.id].r : last.rate;
    out.push({ id: m.id, name: m.name, rate: inr(cur), week: fmtWeek(last.week), delta: delta(dPct(last.rate, prev ? prev.rate : null)) });
  });
  return out;
}

export interface WeekRow {
  /** week start, YYYY-MM-DD */
  id: string;
  week: string;
  rate: string;
  delta: Delta;
}

/** Single-material view: one row per week, newest first. */
export function buildWeekRows(st: Pick<AppState, 'rateHistory'>, matId: string): WeekRow[] {
  const ser = weeklySeries(st, matId);
  return ser
    .map((p, i) => {
      const prev = i > 0 ? ser[i - 1].rate : null;
      return { id: p.week, week: fmtWeek(p.week), rate: inr(p.rate), delta: delta(dPct(p.rate, prev)) };
    })
    .reverse();
}

// ---------- "rates due" banner ----------

export interface DueBanner {
  text: string;
  /** no effective update in the last 7 days (amber); otherwise green */
  overdue: boolean;
}

/** Days since the most recent effective rate change; due again after 7 days. */
export function dueBanner(hist: RateHistoryEntry[] | undefined, today: string = todayISO()): DueBanner {
  const list = hist || [];
  const hasHist = list.length > 0;
  let lastEff = '';
  list.forEach((h) => {
    const e = h.eff || h.d;
    if (e <= today && e > lastEff) lastEff = e;
  });
  const dueDays = lastEff ? Math.round((new Date(today).getTime() - new Date(lastEff).getTime()) / 86400000) : null;
  const overdue = dueDays == null || dueDays >= 7;
  const text = !hasHist
    ? 'No rate history yet — save your first weekly update to start the trend.'
    : dueDays == null
      ? 'No rate update in effect yet — update due.'
      : dueDays === 0
      ? 'Rates updated today.'
      : 'Rates last updated ' + String(dueDays) + ' day' + (dueDays === 1 ? '' : 's') + ' ago' + (overdue ? ' — update due.' : '.');
  return { text, overdue };
}

// ---------- bulk update ----------

export interface BulkRow {
  id: string;
  name: string;
  /** current effective rate */
  cur: string;
  draft: NumInput;
  /** change vs current; empty text when no new rate is entered */
  delta: Delta;
}

export interface BulkSec {
  id: string;
  label: string;
  items: BulkRow[];
}

export interface BulkVM {
  eff: string;
  /** post-dated batch: saved to history, applied from the effective date */
  future: boolean;
  count: number;
  secs: BulkSec[];
}

export function buildBulk(st: RatesCtx & Pick<AppState, 'bulkEff' | 'bulkDraft'>, today: string = todayISO()): BulkVM {
  const emap = effMap(st, today);
  const eff = st.bulkEff || today;
  const draft = st.bulkDraft || {};
  const count = Object.keys(draft).filter((id) => num(draft[id]) > 0).length;
  const secs = st.matSections.map((sec) => ({
    id: sec.id,
    label: sec.label,
    items: sec.items.map((it): BulkRow => {
      const cur = emap[it.id] ? emap[it.id].r : num(it.rate);
      const nv = draft[it.id];
      const nvn = num(nv);
      const d = nvn > 0 ? dPct(nvn, cur) : null;
      return { id: it.id, name: it.name, cur: inr(cur), draft: nv == null ? '' : nv, delta: { text: nvn > 0 ? dTxt(d) : '', tone: dTone(d) } };
    }),
  }));
  return { eff, future: eff > today, count, secs };
}

export function bulkFlashText(n: number): string {
  return n > 0 ? 'Saved ' + n + ' rate' + (n === 1 ? '' : 's') : '';
}
