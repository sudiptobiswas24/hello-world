import { cleanup, render } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it } from "vitest";

import { FollowUpLink } from "./Home";

afterEach(cleanup);

function shown(link: string) {
  return render(<MemoryRouter><FollowUpLink link={link} summary="Call back" /></MemoryRouter>).container;
}

describe("a follow-up on Home (O317)", () => {
  it("links to a place on this site", () => {
    expect(shown("/app/sales/customers/1").querySelector("a")?.getAttribute("href")).toBe("/app/sales/customers/1");
  });

  it("shows a row stored before the server's rule as text, never as a link off the site", () => {
    for (const link of ["/\\pay-vendor.example/login", "/\t/pay-vendor.example/login", "//pay-vendor.example",
      "https://pay-vendor.example/login", "/%5cpay-vendor.example"]) {
      const page = shown(link);
      expect(page.querySelector("a"), link).toBeNull();
      expect(page.textContent).toBe("Call back");
      cleanup();
    }
  });
});
