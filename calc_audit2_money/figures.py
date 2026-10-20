# Expected figures worked in plain Python, before any probe.
# Table 13: October 2026 has 2 real invoices and 1 opening-balance invoice (old system's, dated 2026-10-02).
real, opening = 2, 1
print("Table 13 invoices issued by this system's GSTIN in the month:", real, "(opening invoice is the old system's)")
# Credit of an opening balance (plain) is no supply: credit notes in table 13 = 0
print("credit notes counted:", 0)
# M5: KA bill 10,000 at 18% IGST = 1,800 booked; the 2B line says ITC not available: matched credit should be 0.
print("M5 igst:", 10000 * 18 // 100)
# M9: statement file of 2 rows; two simultaneous imports should leave 2 lines, not 4.
print("M9 lines:", 2)
# M12: invoice total == receivable debit == GSTR-1 value == items + tax, to the paisa, 40 random invoices.
# M13: EUR 1000.07 in 3-5 parts, rates 1.0 to 1.4: receivable nets to 0 once nothing is due.
