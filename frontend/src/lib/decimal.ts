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

/** A decimal string as a whole number scaled by 10^places, rounded half away from zero. */
function scaled(value: string | number | null | undefined, places: number): bigint {
  const match = String(value ?? "0").trim().match(/^([+-]?)(\d*)(?:\.(\d*))?$/);
  if (!match) throw new Error(`Not a number: ${value}`);
  const fraction = (match[3] ?? "").padEnd(places + 1, "0");
  let size = BigInt(match[2] || "0") * 10n ** BigInt(places) + BigInt(fraction.slice(0, places) || "0");
  if (Number(fraction[places]) >= 5) size += 1n;
  return match[1] === "-" ? -size : size;
}

/** `whole` / `by` to `places`, rounded half away from zero, as a string. */
export function divide(whole: bigint, by: bigint, places: number): string {
  if (by === 0n) throw new Error("Division by nothing");
  const negative = (whole < 0n) !== (by < 0n);
  const [a, b] = [whole < 0n ? -whole : whole, by < 0n ? -by : by];
  const unit = 10n ** BigInt(places);
  const quotient = (a * unit * 2n + b) / (b * 2n);
  const text = places ? `${quotient / unit}.${String(quotient % unit).padStart(places, "0")}` : String(quotient);
  return negative && quotient !== 0n ? `-${text}` : text;
}

/** Minutes, as a quantity string, in hours to one place: "90.00" reads "1.5". */
export function minutesAsHours(minutes: string | number | null | undefined): string {
  if (minutes === null || minutes === undefined || minutes === "") return "";
  return divide(scaled(minutes, 4), 600000n, 1); // minutes x 10^4, over 60 x 10^4
}

/** A ratio ("0.8234") as a percentage to one place ("82.3%"); a missing one reads "—". */
export function asPercent(ratio: string | number | null | undefined): string {
  if (ratio === null || ratio === undefined || ratio === "") return "—";
  return `${divide(scaled(ratio, 6), 10000n, 1)}%`; // ratio x 10^6 x 100, over 10^6
}
