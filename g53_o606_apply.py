import sys
root = sys.argv[1]

def edit(path, pairs):
    p = f"{root}/{path}"
    s = open(p).read()
    for old, new in pairs:
        assert s.count(old) == 1, (path, old[:70])
        s = s.replace(old, new)
    open(p, "w").write(s)

edit("apps/core/documents.py", [
('''def registered_gstin(company):
    """The GST registration's GSTIN where there is one; else the company's own tax ID, for a plant not registered."""
    for ask in _REGISTERED_GSTIN:
        gstin = ask(company)
        if gstin:
            return gstin
    return company.tax_id or ""
''',
'''def registered_tax_number(company):
    """
    (number, label): the GST registration's GSTIN, labelled "GSTIN", where there is one; else the company's own tax
    number, labelled "Tax ID", for a plant not registered (O606: it printed as a GSTIN on every letterhead).
    """
    for ask in _REGISTERED_GSTIN:
        gstin = ask(company)
        if gstin:
            return gstin, "GSTIN"
    return company.tax_id or "", "Tax ID"


def registered_gstin(company):
    """The GST registration's GSTIN where there is one; else the company's own tax ID, for a plant not registered."""
    return registered_tax_number(company)[0]
'''),
('''    demo plant's purchase orders and challans), and a different one where the two disagreed.
    """
    return {"name": company.name, "legal_name": company.legal_name or "",
            "address": company.address.formatted().splitlines() if company.address_id else [],
            "tax_id": registered_gstin(company), "contacts": contacts(company)}
''',
'''    demo plant's purchase orders and challans), and a different one where the two disagreed.

    What the number is goes with it (O606): a plant not registered keeps its own tax number as a "Tax ID", printed
    as one rather than as a GSTIN. A letterhead kept before that has no label, and every number it kept was printed
    as a GSTIN, so it reprints as one.
    """
    number, label = registered_tax_number(company)
    return {"name": company.name, "legal_name": company.legal_name or "",
            "address": company.address.formatted().splitlines() if company.address_id else [],
            "tax_id": number, "tax_label": label, "contacts": contacts(company)}
'''),
('''    if kept.get("tax_id"):
        parts.append(f"GSTIN {kept['tax_id']}")
    return lines(parts)
''',
'''    if kept.get("tax_id"):
        parts.append(f"{kept.get('tax_label', 'GSTIN')} {kept['tax_id']}")
    return lines(parts)
'''),
])

edit("apps/gst/tests_rcm_cash.py", [
('''                      "address": ["Plot 14, MIDC", "Aurangabad"], "tax_id": old, "contacts": ["0240 248 1100"]}
''',
'''                      "address": ["Plot 14, MIDC", "Aurangabad"], "tax_id": old, "tax_label": "GSTIN",
                      "contacts": ["0240 248 1100"]}
'''),
])

edit("apps/accounting/tests_company_gstin.py", [
("from apps.core.documents import issuer, letterhead, printed_footer\n",
 "from apps.core.documents import issuer, letterhead, printed_footer, printed_letterhead\n"),
('''        self.assertEqual(issuer(self.company)["tax_id"], "DE123456789")
''',
'''        self.assertEqual(issuer(self.company)["tax_id"], "DE123456789")
        # O606: printed as what it is, not as a GSTIN; a letterhead kept before the label was, reprints as it did.
        self.assertIn("Tax ID DE123456789", str(letterhead(self.company)))
        self.assertNotIn("GSTIN", str(letterhead(self.company)))
        kept_before = {key: value for key, value in issuer(self.company).items() if key != "tax_label"}
        self.assertIn("GSTIN DE123456789", str(printed_letterhead(kept_before)))
'''),
])
print("applied")
