import { describe, expect, it } from "vitest";

import { sitePath } from "./sitePath";

describe("sitePath", () => {
  it("keeps a place in the application as it is", () => {
    expect(sitePath("/app/sales/customers/1?tab=notes")).toBe("/app/sales/customers/1?tab=notes");
    expect(sitePath("/app/?q=a%20b")).toBe("/app/?q=a%20b");
  });

  it("renders nothing that leads off the site, a row stored before the server's rule included", () => {
    for (const link of ["https://pay-vendor.example/login", "//pay-vendor.example/login", "javascript:alert(1)",
      "/\\pay-vendor.example/login", "/\t/pay-vendor.example/login", "/%5cpay-vendor.example/login",
      "/%2f/pay-vendor.example/login", "/%252f%252fpay-vendor.example/login", "/\n/pay-vendor.example",
      "app/sales", "", "/%E0%A4%A"]) {
      expect(sitePath(link), link).toBeNull();
    }
    expect(sitePath(null)).toBeNull();
    expect(sitePath(undefined)).toBeNull();
  });
});
