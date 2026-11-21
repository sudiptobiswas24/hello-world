import { describe, expect, it } from "vitest";

import { receivableWarehouse, usableWarehouse } from "./WarehouseChoice";

// O612: a receipt was offered only the company's own stock, while posting takes quarantine (where goods made from
// held batches must land) and the vendor's own consignment store, and refuses a customer's material and another
// vendor's consignment store.
const plain = { is_active: true, is_quarantine: false, is_transit: false, consignment_vendor: null, held_for: null };

describe("what a receipt may be put into", () => {
  it("takes the company's own stock and quarantine", () => {
    expect(receivableWarehouse(plain)).toBe(true);
    expect(receivableWarehouse({ ...plain, is_quarantine: true })).toBe(true);
  });

  it("takes its own vendor's consignment store, and no other's", () => {
    const store = { ...plain, consignment_vendor: 7 };
    expect(receivableWarehouse(store, 7)).toBe(true);
    expect(receivableWarehouse(store, 8)).toBe(false);
    expect(receivableWarehouse(store, null)).toBe(false);
  });

  it("never a customer's material, transit or a closed warehouse", () => {
    expect(receivableWarehouse({ ...plain, held_for: 3 }, 7)).toBe(false);
    expect(receivableWarehouse({ ...plain, is_transit: true })).toBe(false);
    expect(receivableWarehouse({ ...plain, is_active: false })).toBe(false);
  });

  it("while a delivery still ships only from the company's own stock", () => {
    expect(usableWarehouse({ ...plain, is_quarantine: true })).toBe(false);
    expect(usableWarehouse({ ...plain, consignment_vendor: 7 })).toBe(false);
  });
});
