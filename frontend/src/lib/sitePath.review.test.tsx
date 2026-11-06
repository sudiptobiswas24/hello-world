import { render } from "@testing-library/react";
import { MemoryRouter, Link } from "react-router";
import { describe, expect, it } from "vitest";
import { sitePath } from "./sitePath";

describe("review_sechr O317", () => {
  for (const link of ["/.//pay-vendor.example/login", "/x/..//pay-vendor.example", "/%2e//pay-vendor.example"]) {
    it(`sitePath(${link}) is not a protocol-relative path`, () => {
      const out = sitePath(link);
      expect(out === null || !out.startsWith("//")).toBe(true);
    });
    it(`rendered ${link} has no off-site href`, () => {
      const to = sitePath(link);
      if (!to) return;
      const { container } = render(<MemoryRouter><Link to={to}>x</Link></MemoryRouter>);
      const href = container.querySelector("a")?.getAttribute("href") ?? "";
      expect(href.startsWith("//")).toBe(false);
    });
  }
});
