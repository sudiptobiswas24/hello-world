import unicodedata

from django.db import migrations


def document_number(number):
    """Frozen copy of accounting.gst.document_number as this migration keyed (g22, review F5)."""
    text = unicodedata.normalize("NFKC", number or "")
    text = "".join("-" if unicodedata.category(ch) == "Pd" or ch == "−" else ch
                   for ch in text if unicodedata.category(ch) != "Cf")
    return " ".join(text.split()).upper()


def keyed(number, day):
    key = document_number(number)
    if not key:
        return ""
    if day is None:
        return f":{key}"
    return f"{day.year if day.month >= 4 else day.year - 1}:{key}"


def vendor_invoice_key(number, day):
    """Frozen copy of purchasing.models.vendor_invoice_key as this migration keyed."""
    return keyed(number, day)


def supplier_note_key(number, day):
    """Frozen copy of purchasing.models.supplier_note_key as this migration keyed."""
    return keyed(number, day)


def rekey(apps, schema_editor):
    """
    Every bill's vendor invoice number and every debit note's supplier credit
    note, keyed again by the one rule (g22 decision): separators kept as
    written, so "INV-77" and "INV/77", made one by 0076, are two again; case,
    whitespace, Unicode forms and dashes folded. Every key is cleared first, so
    no row meets another's old key. Where the rule makes two one, the earliest
    keeps the key and the rest are left unkeyed and printed, never merged.
    """
    Bill = apps.get_model("purchasing", "Bill")
    Bill.objects.update(reference_key="", supplier_note_key="")
    kept, left = {}, []
    for bill in Bill.objects.only("vendor_id", "number", "debits_id", "reference", "bill_date", "supplier_note_number",
                                  "supplier_note_date").order_by("bill_date", "pk"):
        if bill.debits_id is None and bill.reference:
            key = vendor_invoice_key(bill.reference, bill.bill_date)
            first = kept.setdefault(("invoice", bill.vendor_id, key), bill) if key else bill
            if first.pk != bill.pk:
                left.append(("bills vendor invoice", bill.reference, first.reference, first, bill))
            elif key:
                Bill.objects.filter(pk=bill.pk).update(reference_key=key)
        if bill.supplier_note_number:
            key = supplier_note_key(bill.supplier_note_number, bill.supplier_note_date or bill.bill_date)
            if bill.debits_id and key:
                first = kept.setdefault(("note", bill.vendor_id, key), bill)
                if first.pk != bill.pk:
                    left.append(("answers supplier credit note", bill.supplier_note_number,
                                 first.supplier_note_number, first, bill))
                    continue
            Bill.objects.filter(pk=bill.pk).update(supplier_note_key=key)
    for what, number, theirs, first, bill in left:
        print(f"purchasing 0079: {bill.number or bill.pk} {what} {number!r}, which is {theirs!r} on "
              f"{first.number or first.pk} of the same vendor and year; left unkeyed, not merged.")


class Migration(migrations.Migration):

    dependencies = [
        ("purchasing", "0078_a_bill_keeps_the_tcs_its_supplier_collected"),
    ]

    operations = [
        migrations.RunPython(rekey, migrations.RunPython.noop),
    ]
