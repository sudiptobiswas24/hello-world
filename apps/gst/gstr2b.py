"""
Input credit against GSTR-2B.

Credit on a purchase may be taken only once the supplier has filed the
invoice and it shows in the company's GSTR-2B for a month. The books
say what was booked; the portal's file says what was filed; this keeps
the file (a fact, like a bank statement) and matches the two: a bill
with its 2B line and the same figures; one whose figures differ; a 2B
line with no bill behind it; a bill the portal has not seen, whose
credit waits. Carried across the year: a bill from April unmatched in
May stays listed until some month's 2B carries it, and a 2B line with
no bill stays until one is posted.

The file is the portal's own JSON download, or its B2B and CDNR sheets
saved as CSV. Nothing here changes GSTR-3B: what is claimed stays what
the books say, and the officer reads the waiting figure beside it.
"""

import datetime
import json
import re
from collections import OrderedDict
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction

from apps.core import csvrows
from apps.core.models import AuditModel, Company

from .returns import HEADS, _bills, _settings, month

PAISA = Decimal("0.01")
ZERO = Decimal("0")
# Rounding between a supplier's return and a typed bill: within this,
# the figures are taken as the same.
NEAR = Decimal("1.00")
KINDS = [("invoice", "Invoice"), ("credit_note", "Credit note"), ("debit_note", "Debit note")]
FIGURES = ("taxable", *HEADS, "value")
# Tables of the download that are not supplier invoices: counted, not read.
OTHER_TABLES = ("isd", "isda", "impg", "impgsez", "ecom", "ecoma")


class Gstr2bStatement(AuditModel):
    """One month's GSTR-2B as downloaded, kept whole."""

    period = models.CharField(max_length=7, unique=True, help_text="YYYY-MM")
    gstin = models.CharField(max_length=15, blank=True, help_text="The registration the file is for.")
    generated_on = models.DateField(null=True, blank=True, help_text="The portal's generation date.")
    source = models.CharField(max_length=8, blank=True, help_text="json or csv")
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-period"]
        verbose_name = "GSTR-2B statement"

    def __str__(self):
        return f"GSTR-2B {self.period}"


class Gstr2bLine(models.Model):
    """One supplier document in the 2B: an invoice, a credit note or a debit note."""

    statement = models.ForeignKey(Gstr2bStatement, on_delete=models.CASCADE, related_name="lines")
    supplier_gstin = models.CharField(max_length=15)
    supplier_name = models.CharField(max_length=255, blank=True)
    kind = models.CharField(max_length=16, choices=KINDS, default="invoice")
    number = models.CharField(max_length=64)
    date = models.DateField(null=True, blank=True)
    value = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    taxable = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    igst = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    cgst = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    sgst = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    cess = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    reverse_charge = models.BooleanField(default=False)
    itc_available = models.BooleanField(default=True)
    reason = models.CharField(max_length=255, blank=True, help_text="Why the portal says the credit is not available.")
    irn = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["supplier_gstin", "date", "number"]
        constraints = [
            # Every line carries its number (the readers refuse one without);
            # the condition is the shape every numbered row here has.
            models.UniqueConstraint(fields=["statement", "supplier_gstin", "kind", "number"],
                                    condition=~models.Q(number=""), name="one_2b_line_per_document"),
        ]

    def __str__(self):
        return f"{self.supplier_gstin} {self.number}"

    def itc(self):
        return sum((getattr(self, head) for head in HEADS), ZERO)


# -- reading the file ----------------------------------------------------


def _decimal(value):
    try:
        return Decimal(str(value if value not in (None, "") else 0).replace(",", "")).quantize(PAISA, ROUND_HALF_UP)
    except ArithmeticError:
        raise ValidationError({"text": [f"{value!r} is not an amount."]}) from None


def _day(value):
    for shape in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(str(value or ""), shape).date()
        except ValueError:
            continue
    return None


def _period_of(rtnprd):
    """'092026' → '2026-09'."""
    text = str(rtnprd or "")
    if len(text) == 6 and text.isdigit():
        return f"{text[2:]}-{text[:2]}"
    return ""


def _line(supplier, kind, number, day, entry):
    if not str(number or "").strip():
        raise ValidationError({"text": [f"An entry of {supplier.get('ctin') or 'a supplier'} has no document number."]})
    items = entry.get("items") or []

    def total(head):
        return sum((_decimal(item.get(head)) for item in items), ZERO)

    return {
        "supplier_gstin": str(supplier.get("ctin") or "")[:15], "supplier_name": str(supplier.get("trdnm") or "")[:255],
        "kind": kind, "number": str(number or "")[:64], "date": _day(day), "value": _decimal(entry.get("val")),
        "taxable": total("txval"), "igst": total("igst"), "cgst": total("cgst"), "sgst": total("sgst"),
        "cess": total("cess"), "reverse_charge": str(entry.get("rev") or "N").upper() == "Y",
        "itc_available": str(entry.get("itcavl") or "Y").upper() != "N",
        "reason": str(entry.get("rsn") or "")[:255], "irn": str(entry.get("irn") or "")[:64],
    }


def _read_json(text):
    try:
        payload = json.loads(text)
    except ValueError as error:
        raise ValidationError({"text": [f"Not the portal's JSON: {error}."]})
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    docdata = data.get("docdata") if isinstance(data, dict) else None
    if not isinstance(docdata, dict):
        raise ValidationError({"text": ["The file has no docdata: it is not a GSTR-2B download."]})
    header = {"gstin": str(data.get("gstin") or ""), "period": _period_of(data.get("rtnprd")),
              "generated_on": _day(data.get("gendt"))}
    lines = []
    for table in ("b2b", "b2ba"):
        for supplier in docdata.get(table) or []:
            for entry in supplier.get("inv") or []:
                lines.append(_line(supplier, "invoice", entry.get("inum"), entry.get("idt"), entry))
    for table in ("cdnr", "cdnra"):
        for supplier in docdata.get(table) or []:
            for entry in supplier.get("nt") or []:
                kind = "credit_note" if str(entry.get("nttyp") or "C").upper().startswith("C") else "debit_note"
                lines.append(_line(supplier, kind, entry.get("ntnum"), entry.get("dt"), entry))
    skipped = [f"{table}: {len(docdata[table])} supplier(s) not read" for table in OTHER_TABLES if docdata.get(table)]
    return header, lines, skipped


ROLES = {
    "gstin": ("gstin",), "name": ("trade", "legal", "name"), "number": ("number", "num"),
    "date": ("date",), "value": ("value",), "taxable": ("taxable",),
    "igst": ("integrated", "igst"), "cgst": ("central", "cgst"), "sgst": ("state", "sgst"),
    "cess": ("cess",), "itc": ("itc",), "reason": ("reason",), "reverse": ("reverse",), "irn": ("irn",),
}
# A heading saying one of these is not the role its other words suggest.
NOT_FOR = {"date": ("filing", "irn", "period"), "value": ("taxable",), "irn": ("date",), "number": ("type",)}


def _columns(keys):
    found = {}
    for role, words in ROLES.items():
        for key in keys:
            if any(csvrows.says(key, word) for word in NOT_FOR.get(role, ())):
                continue
            if any(csvrows.says(key, word) for word in words):
                found[role] = key
                break
    kind = next((key for key in keys if csvrows.says(key, "note") and csvrows.says(key, "type")), None)
    if kind:
        found["kind"] = kind
    missing = [role for role in ("gstin", "number", "taxable") if role not in found]
    if missing:
        raise ValidationError({"text": [f"No column names the {', '.join(missing)}; the headings are "
                                        f"{', '.join(keys) or 'missing'}."]})
    return found


def _read_csv(text):
    """The B2B or CDNR sheet of the portal's Excel, saved as CSV: one row a rate, summed per document."""
    rows_of_text = text.splitlines()
    start = next((index for index, row in enumerate(rows_of_text) if "gstin" in row.lower()), None)
    if start is None:
        raise ValidationError({"text": ["No row of headings names the supplier's GSTIN: this is not the "
                                        "2B's B2B or CDNR sheet."]})
    rows = csvrows.read("\n".join(rows_of_text[start:]))
    if not rows:
        raise ValidationError({"text": ["The file has no rows under its headings."]})
    columns = _columns(list(rows[0].keys()))
    documents = OrderedDict()
    for number, row in enumerate(rows, start=start + 2):
        if not any(row.values()):
            continue
        try:
            gstin = csvrows.required(row, columns["gstin"])[:15]
            document_number = csvrows.required(row, columns["number"])[:64]
            kind = "invoice"
            if "kind" in columns:
                said = row.get(columns["kind"], "").upper()
                kind = "credit_note" if said.startswith("C") else "debit_note" if said.startswith("D") else "invoice"
            key = (gstin, kind, document_number)
            if key not in documents:
                documents[key] = {
                    "supplier_gstin": gstin, "supplier_name": row.get(columns.get("name", ""), "")[:255],
                    "kind": kind, "number": document_number,
                    "date": csvrows.date(row, columns["date"], required_=False) if "date" in columns else None,
                    "value": csvrows.decimal(row, columns["value"]) or ZERO if "value" in columns else ZERO,
                    "taxable": ZERO, "igst": ZERO, "cgst": ZERO, "sgst": ZERO, "cess": ZERO,
                    "reverse_charge": row.get(columns.get("reverse", ""), "").lower().startswith("y"),
                    "itc_available": not row.get(columns.get("itc", ""), "").lower().startswith("n"),
                    "reason": row.get(columns.get("reason", ""), "")[:255],
                    "irn": row.get(columns.get("irn", ""), "")[:64],
                }
            line = documents[key]
            for figure in ("taxable", *HEADS):
                if figure in columns:
                    line[figure] += csvrows.decimal(row, columns[figure]) or ZERO
        except csvrows.RowError as error:
            raise ValidationError({"text": [f"Row {number}, {error.column}: {error.message}"]})
    return {"gstin": "", "period": "", "generated_on": None}, list(documents.values()), []


def read(text):
    """({gstin, period, generated_on}, [line dicts], [what was skipped]) of a 2B file, JSON or CSV."""
    text = (text or "").lstrip("﻿ \r\n\t")
    if not text:
        raise ValidationError({"text": ["The file is empty."]})
    if text.startswith("{"):
        return _read_json(text)
    return _read_csv(text)


def keep(period, text, *, user=None, replace=False):
    """
    Keep a month's 2B. `period` may be left to the file (the JSON names
    it); a file for another month or another registration is refused.
    A month already kept is kept again only with `replace`.
    """
    settings = _settings()
    header, lines, skipped = read(text)
    period = period or header["period"]
    if not period:
        raise ValidationError({"period": ["Say which month this file is, as YYYY-MM; a CSV does not."]})
    month(period)
    if header["period"] and header["period"] != period:
        raise ValidationError({"period": [f"This file is {header['period']}'s GSTR-2B, not {period}'s."]})
    if header["gstin"] and header["gstin"] != settings.gstin:
        raise ValidationError({"text": [f"This file is for GSTIN {header['gstin']}; the registration here is "
                                        f"{settings.gstin}."]})
    if not lines:
        raise ValidationError({"text": ["The file holds no supplier invoices or notes."]})
    existing = Gstr2bStatement.objects.filter(period=period).first()
    if existing is not None and not replace:
        raise ValidationError({"period": [f"{period}'s GSTR-2B is already kept, with {existing.lines.count()} "
                                          "line(s). Say so to replace it with this file."]})
    # An amendment of a document carries the same number: the later entry wins.
    unique = OrderedDict()
    for line in lines:
        unique[(line["supplier_gstin"], line["kind"], line["number"])] = line
    with transaction.atomic():
        if existing is not None:
            existing.delete()
        statement = Gstr2bStatement.objects.create(
            period=period, gstin=header["gstin"] or settings.gstin, generated_on=header["generated_on"],
            source="json" if text.lstrip("﻿ \r\n\t").startswith("{") else "csv",
            created_by=user, updated_by=user)
        Gstr2bLine.objects.bulk_create([Gstr2bLine(statement=statement, **line) for line in unique.values()])
    return statement, skipped


# -- matching ------------------------------------------------------------


def normalise(number):
    """
    INV/2026-27/0012 and INV-2026-27-12 are one number to a clerk: the
    letters and the digit runs, without their separators or leading
    zeros. GSTN compares exactly; the figures are compared after.
    """
    parts = re.findall(r"[A-Z]+|\d+", (number or "").upper())
    return "".join(part.lstrip("0") or "0" if part.isdigit() else part for part in parts)


def _figures(document):
    """A bill's recorded figures as the 2B shows them: tax under reverse charge included."""
    figures = {"taxable": sum((line.taxable for line in document.lines), ZERO), "value": document.value}
    for head in HEADS:
        figures[head] = sum((getattr(line, head) + line.reverse_charge.get(head, ZERO) for line in document.lines), ZERO)
    return figures


def _line_figures(line):
    return {figure: getattr(line, figure) for figure in FIGURES}


def _money(figures):
    return sum((figures[head] for head in HEADS), ZERO)


def _group(kind):
    return "note" if kind == "credit_note" else "invoice"


def _bill_row(document, figures):
    source = document.source
    return {"id": source.pk, "number": source.number, "vendor": str(source.vendor), "reference": source.reference,
            "supplier_note_number": source.supplier_note_number, "date": document.date,
            "is_note": document.is_note, **figures}


def _as_filed(document):
    """
    (number, date) the supplier filed the document under: a bill's reference
    and date; a debit note's, the supplier's credit note it records
    (Bill.supplier_note_number), not the reference it copied from its bill.
    """
    source = document.source
    if document.is_note:
        return source.supplier_note_number, source.supplier_note_date or document.date
    return source.reference, document.date


def _line_row(line):
    return {"id": line.pk, "statement": line.statement.period, "number": line.number, "date": line.date,
            "supplier_gstin": line.supplier_gstin, "supplier_name": line.supplier_name, "kind": line.kind,
            "itc_available": line.itc_available, "reason": line.reason, "reverse_charge": line.reverse_charge,
            "irn": line.irn, **_line_figures(line)}


def reconcile(period):
    """
    {period, statement, matched, differs, not_in_2b, not_booked, totals,
    notes}: the month's 2B against the posted bills of the fiscal year
    to its end, and every earlier month's 2B of the year with them.
    """
    settings = _settings()
    start, end = month(period)
    fy_start, _ = Company.get().fiscal_year_bounds(start)
    statements = {row.period: row for row in Gstr2bStatement.objects.filter(period__gte=f"{fy_start:%Y-%m}",
                                                                              period__lte=period)}
    lines = list(Gstr2bLine.objects.filter(statement__period__in=statements).select_related("statement"))
    documents = [document for document in _bills(fy_start, end, settings.state) if document.registered]
    outside = sum(1 for document in documents if document.registration != "regular")
    documents = [document for document in documents if document.registration == "regular"]

    by_number = {}
    for line in lines:
        by_number.setdefault((line.supplier_gstin, _group(line.kind), normalise(line.number)), line)
    waiting = {line.pk: line for line in lines}
    result = {"period": period, "statement": None, "matched": [], "differs": [], "not_in_2b": [], "not_booked": [],
              "notes": []}
    if period in statements:
        statement = statements[period]
        result["statement"] = {"id": statement.pk, "period": period, "gstin": statement.gstin,
                               "generated_on": statement.generated_on, "lines": sum(1 for line in lines
                                                                                     if line.statement_id == statement.pk)}
    else:
        result["notes"].append(f"No GSTR-2B is kept for {period}: every bill of the month reads as waiting.")
    if outside:
        result["notes"].append(f"{outside} bill(s) from composition or SEZ suppliers are outside the 2B's "
                               "supplier tables and are not matched.")
    totals = {key: ZERO for key in ("filed", "booked", "matched", "waiting", "not_booked")}
    for document in sorted(documents, key=lambda row: (row.date, row.number)):
        figures = _figures(document)
        sign = -1 if document.is_note else 1
        if start <= document.date <= end:
            totals["booked"] += sign * _money(figures)
        group = _group("credit_note" if document.is_note else "invoice")
        number, filed_on = _as_filed(document)
        typed = normalise(number)
        line = by_number.get((document.gstin, group, typed)) if typed else None
        if line is not None and line.pk not in waiting:
            line = None
        if line is None:
            # The number typed differently: the same supplier's document of
            # that day for that value is the one.
            line = next((row for row in waiting.values()
                         if row.supplier_gstin == document.gstin and _group(row.kind) == group
                         and row.date == filed_on and row.value == figures["value"]), None)
        if line is None:
            result["not_in_2b"].append({"bill": _bill_row(document, figures), "line": None})
            totals["waiting"] += sign * _money(figures)
            continue
        del waiting[line.pk]
        filed = _line_figures(line)
        difference = {figure: filed[figure] - figures[figure] for figure in FIGURES}
        row = {"bill": _bill_row(document, figures), "line": _line_row(line), "difference": difference}
        if all(abs(delta) <= NEAR for delta in difference.values()):
            result["matched"].append(row)
        else:
            result["differs"].append(row)
        if line.statement.period == period:
            totals["matched"] += sign * _money(figures)
    for line in sorted(waiting.values(), key=lambda row: (row.statement.period, row.supplier_gstin, row.number)):
        result["not_booked"].append({"bill": None, "line": _line_row(line)})
        totals["not_booked"] += (-1 if line.kind == "credit_note" else 1) * line.itc()
    for line in lines:
        if line.statement.period == period and line.itc_available:
            totals["filed"] += (-1 if line.kind == "credit_note" else 1) * line.itc()
    result["totals"] = totals
    return result
