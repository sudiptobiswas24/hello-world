/**
 * Sums of money done exactly, in whole paise (BigInt), never in floats.
 * For what a screen must work out itself: what is left of a receipt to
 * apply. Everything else the server sums.
 */

export function toPaise(value: string | null | undefined): bigint {
  if (!value) return 0n;
  const match = String(value).trim().match(/^([+-]?)(\d*)(?:\.(\d*))?$/);
  if (!match) throw new Error(`Not an amount: ${value}`);
  const whole = BigInt(match[2] || "0");
  const fraction = (match[3] ?? "").padEnd(3, "0");
  let paise = whole * 100n + BigInt(fraction.slice(0, 2));
  if (Number(fraction[2]) >= 5) paise += 1n; // half away from zero
  return match[1] === "-" ? -paise : paise;
}

export function fromPaise(paise: bigint): string {
  const negative = paise < 0n;
  const size = negative ? -paise : paise;
  const text = `${size / 100n}.${String(size % 100n).padStart(2, "0")}`;
  return negative ? `-${text}` : text;
}

export function sum(values: (string | null | undefined)[]): string {
  return fromPaise(values.reduce((total, value) => total + toPaise(value), 0n));
}

export function minus(a: string, b: string): string {
  return fromPaise(toPaise(a) - toPaise(b));
}

export function least(a: string, b: string): string {
  return toPaise(a) <= toPaise(b) ? fromPaise(toPaise(a)) : fromPaise(toPaise(b));
}

export function positive(value: string): boolean {
  return toPaise(value) > 0n;
}

/** Above zero at whatever places it is written: for quantities, which carry four. */
export function aboveZero(value: string | null | undefined): boolean {
  const match = String(value ?? "").trim().match(/^([+-]?)(\d*)(?:\.(\d*))?$/);
  if (!match) throw new Error(`Not a number: ${value}`);
  return match[1] !== "-" && /[1-9]/.test(`${match[2]}${match[3] ?? ""}`);
}
