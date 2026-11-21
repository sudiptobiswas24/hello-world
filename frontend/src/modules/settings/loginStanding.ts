/**
 * Whether a login still signs in, as the server says. `ended` is derived there
 * from the rule sign-in asks (a leaving date before the plant's today, O333):
 * a login switched on whose person has left reads Ended, not Active, though
 * nothing has switched it off. The screen decides nothing itself.
 */
export type Standing = { label: "Active" | "Deactivated" | "Ended"; tone: string; why: string };

export function loginStanding(row: Record<string, unknown>): Standing {
  if (row.is_active === false) return { label: "Deactivated", tone: "draft", why: "Switched off: the login no longer signs in" };
  if (typeof row.ended === "string" && row.ended) return { label: "Ended", tone: "void", why: row.ended };
  return { label: "Active", tone: "open", why: "" };
}
