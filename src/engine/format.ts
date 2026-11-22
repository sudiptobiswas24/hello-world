import type { NumInput, Unit } from './types';

/** Lenient numeric read: blank / invalid input counts as 0. */
export function num(v: NumInput | null | undefined | unknown): number {
  const n = parseFloat(v as string);
  return Number.isFinite(n) ? n : 0;
}

/** Indian-grouped fixed-decimal number; "—" for non-finite values. */
export function fmt(n: number, d: number = 2): string {
  if (!Number.isFinite(n)) return '—';
  return n.toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });
}

/** Rupee amount, e.g. "₹ 1,23,456.00". */
export function inr(n: number, d: number = 2): string {
  return Number.isFinite(n) ? '₹ ' + fmt(n, d) : '—';
}

export const UNIT_FACTOR: Record<Unit, number> = { in: 1, cm: 2.54, mm: 25.4 };

/** Convert a length in `unit` to inches. */
export function toIn(v: number, unit: Unit): number {
  return unit === 'cm' ? v / 2.54 : unit === 'mm' ? v / 25.4 : v;
}

/** Today's date as YYYY-MM-DD (UTC, matching the design's `toISOString` usage). */
export function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}
