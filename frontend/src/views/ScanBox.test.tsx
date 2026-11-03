import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ScanBox } from "./ScanBox";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the camera in ScanBox (O438)", () => {
  it("asks the detector for every code an item's GTIN may be printed as", async () => {
    const asked: string[][] = [];
    vi.stubGlobal("BarcodeDetector", class {
      constructor(options?: { formats?: string[] }) { asked.push(options?.formats ?? []); }
      detect() { return Promise.resolve([]); }
    });
    // A camera that never opens: only the detector's formats are under test.
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true, value: { getUserMedia: () => new Promise(() => undefined) },
    });
    const page = render(<ScanBox label="Item" onScan={() => undefined} />);
    await act(async () => { fireEvent.click(page.getByText("Camera")); });
    expect(asked).toHaveLength(1);
    // Item.gtin takes 8, 12, 13 or 14 digits: GTIN-8 prints as EAN-8, GTIN-12 as UPC-A.
    for (const format of ["code_128", "ean_13", "ean_8", "upc_a", "qr_code"]) {
      expect(asked[0], format).toContain(format);
    }
  });
});
