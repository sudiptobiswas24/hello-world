# g26 brief: fix_scan — scanning in stores (L9)

Read docs/handoff/fix_common.md first; it binds you. Then CLAUDE.md, all of it, especially "Two
people at once", the stock-cost items and "The office application". Then L9 in docs/RISKS.md
"Later items", and what exists already:
- frontend/src/views/ScanBox.tsx (a hand scanner types and presses Enter; a phone's camera through
  BarcodeDetector; one handler for both) and frontend/src/modules/purchasing/ReceiveByScan.tsx
  (scan a purchase order at the gate, a draft receipt opens);
- apps/manufacturing/barcode.py (Code 128 drawn as SVG; printed orders and travellers carry it);
- the stock documents a scan drives: goods receipts, deliveries, material issues, stock counts,
  transfers (apps/inventory, apps/purchasing, apps/sales, apps/manufacturing), bins (StorageBin) and
  lots.

## Setup
- Base: the tip fix_coa (L28) ends on; the integrator names it when starting you. Worktree:
  `git -C /home/user/hello-world worktree add --detach /home/user/work/fix_scan <tip>`.
  Link .venv and frontend/node_modules as fix_common says.
- Budget: about 200 tool calls; commit as you go. Report: /home/user/work/reports/fix_scan.md.
- You are the only agent running. Shared rules that exist; use them, never a second copy:
  - ScanBox for every read; barcode.py for every printed code;
  - apps/quality/release.py, the only answer to "may this lot go" (L28 joins it);
  - cost_of_removing() on every outbound path; FEFO and the existing lot allocation;
  - @serialised, lock_rows, correction_date(), the edit log, AuditableViewSetMixin;
  - src/api/client.ts for every call, src/lib/format.ts for every quantity, the registry with the
    permission the server checks.

## Decided (do not reopen)
1. **One resolver.** `GET /api/inventory/scan/?code=` answers what a code is: an item (by its
   code, or its GTIN), a bin, a lot, or a document number (purchase order, delivery, material
   issue, transfer, count), with its id and what the person may do with it. It answers only what
   the asker may read. Every scan screen asks it; none parses codes itself.
2. **Codes.** An item gains an optional GTIN (unique where given, checked digit verified); its own
   code is its barcode otherwise. Bins and lots print their codes as labels (Code 128 through
   barcode.py; a label sheet for a warehouse's bins and for a receipt's lots).
3. **Scan screens, each driven as the person who does it, on a phone-width screen:**
   - receive: on a draft receipt, scanning an item or lot adds or counts up its line;
   - pick: on a delivery, scanning a lot assigns it to the line for its item, refused if release
     says no or if FEFO would pick another first (say which);
   - issue: on a material issue, the same for production;
   - transfer: scan the from-bin, the lots or items, the to-bin;
   - count: scan a bin, then what is in it, with quantities.
   A scan never posts. Posting stays the document's own button, and its own checks.
4. **Offline-tolerant means counts.** A count's scans are kept on the device while there is no
   network and sent when it returns. Each scan carries a key made on the device, and the server
   records a key once: sent twice (a retry, a second tab), it counts once (double processing,
   the audit list's shape 5). Receive, pick, issue and transfer need the network and say so when
   it is gone. Store the queue in IndexedDB, wrapped so a private window still works online.
5. **Two at once.** Two clerks scanning into one draft at once both land (lock_rows on the line or
   document); a scan into a document posted meanwhile is refused in words.

## Every path to name and test
- refusals first: an unknown code, a code the person may not read, a lot not released, a lot of
  another item, a lot FEFO would not pick, a scan into a posted document, a GTIN with a bad check
  digit, a duplicate GTIN;
- reverse: a scanned line removed before posting; the document's own reversal after posting is
  unchanged;
- the same count scan sent twice counts once (test it through the API with the same key);
- browser tests for each screen as its role, at phone width, with the network cut for the count.
