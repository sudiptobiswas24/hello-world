import { describe, expect, it } from "vitest";
import { loginStanding } from "./loginStanding";

describe("loginStanding (O333)", () => {
  it("reads a switched-on login whose person has left as ended, with the server's reason", () => {
    const why = "Asha (E1) has left (leaving date 08-11-2026); their login no longer signs in or decides for the plant.";
    expect(loginStanding({ is_active: true, ended: why })).toEqual({ label: "Ended", tone: "void", why });
  });

  it("reads a login in effect as active", () => {
    expect(loginStanding({ is_active: true, ended: null }).label).toBe("Active");
  });

  it("reads a switched-off login as deactivated, whatever its leaving date", () => {
    expect(loginStanding({ is_active: false, ended: "has left" }).label).toBe("Deactivated");
  });
});
