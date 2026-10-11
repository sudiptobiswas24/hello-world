/**
 * Figures as people at the plant read them.
 *
 * The server sends every amount and quantity as an exact decimal string.
 * These work on the digits of that string and never turn it into a
 * JavaScript number, which cannot hold most paisa amounts exactly:
 * what is shown is what was booked.
 */

type Decimalish = string | number | null | undefined;

function split(value: Decimalish): { negative: boolean; whole: string; fraction: string } | null {
  if (value === null || value === undefined) return null;
  const text = String(value).trim();
  const match = text.match(/^([+-]?)(\d*)(?:\.(\d*))?$/);
  if (!match || (match[2] === "" && (match[3] ?? "") === "")) return null;
  return { negative: match[1] === "-", whole: match[2] || "0", fraction: match[3] ?? "" };
}

/** Round half away from zero to `places`, on the digits. */
function round(whole: string, fraction: string, places: number): { whole: string; fraction: string } {
  if (fraction.length <= places) return { whole, fraction: fraction.padEnd(places, "0") };
  const kept = fraction.slice(0, places);
  const next = fraction.charCodeAt(places) - 48;
  if (next < 5) return { whole, fraction: kept };
  const digits = (whole + kept).split("").map(Number);
  let i = digits.length - 1;
  while (i >= 0) {
    if (digits[i]! < 9) {
      digits[i]! += 1;
      break;
    }
    digits[i] = 0;
    i -= 1;
  }
  let joined = digits.join("");
  if (i < 0) joined = `1${joined}`;
  const cut = joined.length - places;
  return { whole: joined.slice(0, cut) || "0", fraction: joined.slice(cut) };
}

/** 1234567 -> 12,34,567: the last three digits, then pairs. */
export function groupIndian(whole: string): string {
  const digits = whole.replace(/^0+(?=\d)/, "");
  if (digits.length <= 3) return digits;
  const last = digits.slice(-3);
  const rest = digits.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ",");
  return `${rest},${last}`;
}

function compose(negative: boolean, whole: string, fraction: string): string {
  const isZero = /^0*$/.test(whole) && /^0*$/.test(fraction);
  const sign = negative && !isZero ? "-" : "";
  return `${sign}${groupIndian(whole)}${fraction ? `.${fraction}` : ""}`;
}

/** An amount of money: always `places` decimals, grouped. "" when there is none. */
export function money(value: Decimalish, places = 2): string {
  const parts = split(value);
  if (!parts) return "";
  const rounded = round(parts.whole, parts.fraction, places);
  return compose(parts.negative, rounded.whole, rounded.fraction);
}

/**
 * A quantity: as many decimals as it has, up to `places`, without the
 * trailing zeros a column's scale adds ("100.0000" is 100).
 */
export function quantity(value: Decimalish, places = 4): string {
  const parts = split(value);
  if (!parts) return "";
  const rounded = round(parts.whole, parts.fraction, places);
  return compose(parts.negative, rounded.whole, rounded.fraction.replace(/0+$/, ""));
}

/** Whether an amount is below zero, read from the string. */
export function isNegative(value: Decimalish): boolean {
  const parts = split(value);
  return !!parts && parts.negative && !/^0*$/.test(parts.whole + parts.fraction);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * "2026-10-04" -> "04 Oct 2026". A date is read as written, never through
 * Date(), which would move it a day for anyone west of Greenwich.
 */
export function date(value: string | null | undefined): string {
  if (!value) return "";
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!match) return value;
  const month = MONTHS[Number(match[2]) - 1];
  return month ? `${match[3]} ${month} ${match[1]}` : value;
}

// The plant's time zone, as the server keeps it: told on sign-in (setPlantTimeZone), and Kolkata
// until then. Assumed here, the screens asked for tomorrow's work from 18:30 to midnight GMT on
// any server whose day is not Kolkata's.
let plantZone = "Asia/Kolkata";

export function setPlantTimeZone(zone: string): void {
  plantZone = zone;
}

export function plantTimeZone(): string {
  return plantZone;
}

/** A moment, in the plant's own time. */
export function dateTime(value: string | null | undefined, timeZone = plantTimeZone()): string {
  if (!value) return "";
  const moment = new Date(value);
  if (Number.isNaN(moment.getTime())) return value;
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone, day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
  }).formatToParts(moment);
  const get = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  return `${get("day")} ${get("month")} ${get("year")}, ${get("hour")}:${get("minute")}`;
}

/** 25000 -> "25,000": counts of rows, pages. */
export function count(value: number): string {
  return groupIndian(String(Math.trunc(value)));
}
