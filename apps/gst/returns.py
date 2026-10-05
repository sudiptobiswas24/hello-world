"""
GSTR-1 and GSTR-3B, compiled from what documents recorded when they
posted.

Nothing here reads a party's profile, an item's HSN code or a tax's rate
as they stand today. Each figure is what a document froze when it
posted: its taxes, rates and return columns, the party's GSTIN,
registration and state, the line's HSN. A customer who re-registers in
another state next month does not move last month's sale, and a return
compiled twice for the same period gives the same figures.

A document posted before tax recording existed has none of that, so a
period containing one is refused rather than filled in from today's
configuration.

Built:
- GSTR-1: B2B (with SEZ), B2CL, B2CS, exports, credit notes to
  registered (CDNR) and unregistered (CDNUR) buyers, nil / exempt /
  non-GST, the HSN summary split B2B and B2C, documents issued, and
  advances (table 11: 11A received and not invoiced in the period, 11B
  adjusted against an invoice or refunded).
- GSTR-3B: 3.1(a), (b), (c), (e); 3.2; 4(A)(5); 5.

Not built, and returned as `not_built` rather than left looking
complete: reverse charge (3.1(d), 4(A)(3)), imports (4(A)(1), (2)),
blocked and reversed credit (4(B), 4(D)), amendments, e-commerce operators, and the set-off in table 6.

Nothing is filed. Filing needs GSTN credentials through a GST Suvidha
Provider, which this system does not hold. `gstr1_json` gives the
offline tool's shape; validate it in that tool before uploading.
"""

import calendar
import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.accounting.gst import GstSettings
from apps.accounting.models import round_money
from apps.purchasing.models import Bill
from apps.sales.models import Invoice

ZERO = Decimal("0")
HEADS = ("igst", "cgst", "sgst", "cess")
ZERO_RATED = ("sez", "overseas")
NOT_BUILT = [
    "3.1(d) and 4(A)(3): reverse charge",
    "4(A)(1), 4(A)(2): import of goods and services",
    "4(B), 4(D): credit reversed or ineligible",
    "Amendments to earlier periods",
    "Supplies through e-commerce operators",
    "GSTR-3B table 6: payment and set-off",
]
# The portal refuses a document number longer than this.
NUMBER_LIMIT = 16


def month(period):
    """'2026-09' → (1 Sep 2026, 30 Sep 2026)."""
    try:
        year, number = (int(part) for part in period.split("-"))
        last = calendar.monthrange(year, number)[1]
    except (ValueError, AttributeError):
        raise ValidationError(f"{period!r} is not a period; give it as YYYY-MM.")
    return datetime.date(year, number, 1), datetime.date(year, number, last)


def _money():
    return {head: ZERO for head in HEADS} | {"taxable": ZERO}


def _add(total, line, sign=1):
    total["taxable"] += sign * line.taxable
    for head in HEADS:
        total[head] += sign * getattr(line, head)


@dataclass
class Line:
    hsn: str
    uqc: str
    quantity: Decimal
    nature: str  # taxed, zero_rated, nil, exempt, non_gst
    rate: Decimal
    taxable: Decimal
    igst: Decimal = ZERO
    cgst: Decimal = ZERO
    sgst: Decimal = ZERO
    cess: Decimal = ZERO
    source: object = None
    rates: dict = field(default_factory=dict)


@dataclass
class Document:
    source: object
    is_note: bool
    number: str
    date: datetime.date
    gstin: str
    registration: str
    place: str
    inter: bool
    value: Decimal
    lines: list = field(default_factory=list)

    @property
    def registered(self):
        return bool(self.gstin)

    @property
    def zero_rated(self):
        return self.registration in ZERO_RATED

    def taxed(self):
        return [line for line in self.lines if line.nature in ("taxed", "zero_rated")]

    def igst(self):
        return sum((line.igst for line in self.lines), ZERO)


def _uqc(line):
    """The code of the unit the line's quantity is in: its order's, else the item's."""
    item = getattr(line, "item", None)
    if item is None:
        return "NA"
    order_line = getattr(line, "order_line", None)
    unit = getattr(order_line, "uom", None) or item.uom
    code = getattr(unit, "gst_uqc", None)
    return code.code if code else "OTH"


def _document(source, date, state, is_note):
    rate = source.exchange_rate or Decimal("1")

    def base(amount):
        return round_money(amount * rate)

    place = source.place_of_supply
    document = Document(
        source=source, is_note=is_note, number=source.number, date=date,
        gstin=source.party_gstin, registration=source.party_registration,
        # No place at all is reported as local and flagged: neither answer
        # is known, and inter-state would add a state to table 3.2.
        place=place, inter=(place != state) if place else False,
        value=base(source.total()),
    )
    # In entry order: the e-invoice numbers its items from these.
    for line in sorted(source.lines.all(), key=lambda line: line.pk):
        rows = list(line.recorded_taxes.all())
        gst = [row for row in rows if row.gst_head in HEADS]
        if document.zero_rated:
            nature = "zero_rated"
        elif not rows:
            nature = "exempt"
        elif not gst:
            nature = "non_gst"
        elif all(row.rate == 0 for row in gst):
            nature = "nil"
        else:
            nature = "taxed"
        hsn = line.hsn_code
        uqc = "NA" if hsn.startswith("99") else _uqc(line)
        recorded = Line(
            hsn=hsn, uqc=uqc,
            quantity=ZERO if uqc == "NA" else line.quantity,
            nature=nature,
            rate=sum((row.rate for row in gst if row.gst_head != "cess"), ZERO),
            taxable=base(line.net_amount()),
            source=line,
        )
        for row in gst:
            setattr(recorded, row.gst_head, getattr(recorded, row.gst_head) + base(row.amount))
            recorded.rates[row.gst_head] = recorded.rates.get(row.gst_head, ZERO) + row.rate
        document.lines.append(recorded)
    return document


def _settings():
    settings = GstSettings.active()
    if settings is None:
        raise ValidationError("GST is not set up: there is no active registration.")
    return settings


def _refuse_unrecorded(queryset, what):
    unrecorded = list(queryset.filter(taxes_recorded=False).values_list("number", flat=True))
    if unrecorded:
        raise ValidationError(
            f"{len(unrecorded)} {what} in this period posted before taxes were "
            f"recorded ({', '.join(unrecorded[:10])}); a return cannot be "
            "compiled from figures they never froze."
        )


def _invoices(start, end, state):
    # Down payments carry no GST — none is due on an advance for goods —
    # and neither do the credit notes that give one back.
    # Nor do opening balances brought in at go-live: the old system
    # reported those supplies, in returns already filed.
    queryset = Invoice.objects.filter(
        posted=True, is_down_payment=False, is_opening_balance=False,
        invoice_date__gte=start, invoice_date__lte=end,
    ).exclude(credits__is_down_payment=True).exclude(
        # A plain credit of an opening balance is no supply; a note with
        # its own GST on the old invoice is (credit_old_supply).
        credits__is_opening_balance=True, corrects_old_supply=False)
    _refuse_unrecorded(queryset, "invoices and credit notes")
    queryset = queryset.select_related("credits").prefetch_related(
        "lines__recorded_taxes__tax", "lines__item__uom__gst_uqc",
    ).order_by("invoice_date", "number")
    return [
        _document(invoice, invoice.invoice_date, state, invoice.is_credit_note())
        for invoice in queryset
    ]


def _bills(start, end, state):
    # The mirror of _invoices: a prepayment is no inward supply, and
    # neither is the debit note that gives one back. Counted, its lines
    # carry no tax and read as exempt purchases, netted out of table 5.
    queryset = Bill.objects.filter(
        posted=True, is_prepayment=False, is_opening_balance=False,
        bill_date__gte=start, bill_date__lte=end,
    ).exclude(debits__is_prepayment=True).exclude(
        debits__is_opening_balance=True, corrects_old_supply=False)
    _refuse_unrecorded(queryset, "bills and debit notes")
    queryset = queryset.prefetch_related("lines__recorded_taxes__tax").order_by("bill_date", "number")
    return [_document(bill, bill.bill_date, state, bill.is_debit_note()) for bill in queryset]


def _section(document, settings, value=None):
    """Where an invoice is reported in GSTR-1; `value` when not the document's own."""
    if document.registration == "overseas":
        return "exp"
    if document.registered:
        return "b2b"
    if document.inter and (document.value if value is None else value) > settings.b2cl_limit:
        return "b2cl"
    return "b2cs"


def _items(document):
    """The taxed lines of a document, one row per rate."""
    by_rate = defaultdict(_money)
    for line in document.taxed():
        _add(by_rate[line.rate], line)
    return [{"rate": rate} | totals for rate, totals in sorted(by_rate.items())]


def _head(document):
    return {
        "number": document.number,
        "date": document.date,
        "value": document.value,
        "place_of_supply": document.place,
    }


def gstr1(start, end):
    """
    Outward supplies for the period, table by table.

    A credit note is reported where its invoice was: against a
    registered buyer in CDNR; against a large inter-state or an export
    invoice in CDNUR; against anything in the B2C summary by netting it
    there, which is what the summary is.
    """
    settings = _settings()
    documents = _invoices(start, end, settings.state)
    result = {
        "gstin": settings.gstin, "period": (start, end),
        "b2b": [], "b2cl": [], "exp": [], "b2cs": [], "cdnr": [], "cdnur": [],
        "nil": {}, "hsn_b2b": [], "hsn_b2c": [], "documents": [],
        "advances_received": [], "advances_adjusted": [],
        "warnings": [], "not_built": NOT_BUILT,
    }
    b2cs = defaultdict(_money)
    nil = defaultdict(lambda: {"nil": ZERO, "exempt": ZERO, "non_gst": ZERO})
    hsn = {"hsn_b2b": defaultdict(lambda: _money() | {"quantity": ZERO}),
           "hsn_b2c": defaultdict(lambda: _money() | {"quantity": ZERO})}

    for document in documents:
        sign = -1 if document.is_note else 1
        original = document.source.credits if document.is_note else None
        if original is not None and original.is_opening_balance:
            # The old invoice is here only as what was owed on it: the note
            # froze the same buyer, and says what the old one was for.
            invoice = document
            section = _section(document, settings, value=document.source.old_invoice_value or ZERO)
        else:
            if original is not None and original.taxes_recorded:
                invoice = _document(original, original.invoice_date, settings.state, False)
            else:
                # An invoice or, for a note whose invoice predates recording,
                # the note itself: it froze the same party when it posted.
                invoice = document
            section = _section(invoice, settings)

        if section == "b2cs":
            for line in document.taxed():
                _add(b2cs[(document.inter, document.place, line.rate)], line, sign)
        elif not document.is_note:
            entry = _head(document) | {"items": _items(document)}
            if section == "b2b":
                entry |= {
                    "gstin": document.gstin,
                    "type": (("sez_with_payment" if document.igst() else "sez_without_payment")
                             if document.registration == "sez" else "regular"),
                }
            elif section == "exp":
                entry["type"] = "with_payment" if document.igst() else "without_payment"
            result[section].append(entry)
        else:
            entry = _head(document) | {
                # The old system's number, for a note on one of its invoices.
                "original": original.reference if original.is_opening_balance else original.number,
                "original_date": original.invoice_date,
                "items": _items(document),
            }
            if section == "b2b":
                result["cdnr"].append(entry | {"gstin": document.gstin})
            else:
                kind = "b2cl" if section == "b2cl" else (
                    "exp_with_payment" if invoice.igst() else "exp_without_payment"
                )
                result["cdnur"].append(entry | {"type": kind})

        key = ("inter" if document.inter else "intra",
               "registered" if document.registered else "unregistered")
        for line in document.lines:
            if line.nature in ("nil", "exempt", "non_gst"):
                nil[key][line.nature] += sign * line.taxable

        table = "hsn_b2b" if document.registered else "hsn_b2c"
        for line in document.lines:
            row = hsn[table][(line.hsn, line.uqc, line.rate)]
            _add(row, line, sign)
            row["quantity"] += sign * line.quantity
            if not line.hsn:
                result["warnings"].append(f"{document.number}: a line has no HSN or SAC code.")
            elif line.uqc == "OTH":
                result["warnings"].append(
                    f"{document.number}: HSN {line.hsn} is in a unit with no GST unit code."
                )
        if len(document.number) > NUMBER_LIMIT:
            result["warnings"].append(
                f"{document.number} is longer than the {NUMBER_LIMIT} characters the "
                "portal accepts."
            )
        if not document.place and not document.zero_rated:
            result["warnings"].append(
                f"{document.number} has no place of supply: its party had no state "
                "when it posted."
            )

    result["b2cs"] = [
        {"supply": "inter" if inter else "intra", "place_of_supply": place, "rate": rate}
        | totals
        for (inter, place, rate), totals in sorted(b2cs.items())
        if any(totals.values())
    ]
    result["nil"] = {f"{supply}_{party}": totals for (supply, party), totals in sorted(nil.items())}
    for table, rows in hsn.items():
        result[table] = [
            {"hsn": code, "uqc": uqc, "rate": rate} | totals
            for (code, uqc, rate), totals in sorted(rows.items())
        ]
    result["documents"] = _documents_issued(documents)
    received, adjusted = _advances(start, end, settings.state)
    result["advances_received"], result["advances_adjusted"] = _rows(received), _rows(adjusted)
    result["warnings"] = sorted(set(result["warnings"]))
    return result


def _rows(totals):
    return [
        {"supply": "inter" if inter else "intra", "place_of_supply": place, "rate": rate} | money
        for (inter, place, rate), money in sorted(totals.items())
        if any(money.values())
    ]


def _advances(start, end, state):
    """
    Table 11: tax on advances for services (job work), by place and rate.

    11A is tax on advances received in the period and not adjusted in it:
    what was received less what was drawn down or given back by the end
    of the period. 11B is the tax on earlier advances adjusted in the
    period, against an invoice or by a refund. An advance received and
    invoiced in the same month is in neither, as the portal expects.
    """
    from apps.sales.models import Invoice

    received, adjusted = defaultdict(_money), defaultdict(_money)
    deposits = Invoice.objects.filter(
        posted=True, is_down_payment=True, invoice_date__lte=end,
        lines__recorded_taxes__gst_head__in=HEADS,
    ).distinct().prefetch_related(
        "lines__recorded_taxes", "applications__tax_reversals__tax",
        "credit_notes__lines__recorded_taxes",
    )
    for deposit in deposits:
        document = _document(deposit, deposit.invoice_date, state, False)
        (line,) = document.lines
        key = (document.inter, document.place, line.rate)
        rate = deposit.exchange_rate or Decimal("1")
        events = []
        for application in deposit.applications.all():
            event = _money()
            for row in application.tax_reversals.all():
                if row.tax.gst_head in HEADS:
                    event[row.tax.gst_head] += round_money(row.amount * rate)
            event["taxable"] = round_money(application.amount * rate) - sum(event[head] for head in HEADS)
            events.append((application.date, event))
        for note in deposit.credit_notes.all():
            if note.posted:
                (returned,) = _document(note, note.invoice_date, state, True).lines
                event = _money()
                _add(event, returned)
                events.append((note.invoice_date, event))
        if start <= deposit.invoice_date:
            row = received[key]
            _add(row, line)
            for when, event in events:
                if when <= end:
                    for column in event:
                        row[column] -= event[column]
        else:
            for when, event in events:
                if start <= when <= end:
                    for column in event:
                        adjusted[key][column] += event[column]
    return received, adjusted


def _documents_issued(documents):
    series = defaultdict(list)
    for document in documents:
        series["credit_notes" if document.is_note else "invoices"].append(document.number)
    return [
        {"kind": kind, "from": min(numbers), "to": max(numbers), "total": len(numbers),
         "cancelled": 0}
        for kind, numbers in sorted(series.items())
    ]


def gstr3b(start, end):
    """
    The monthly summary: tax on outward supplies, and input tax credit.

    Credit is claimed only on a bill from a registered, regular supplier.
    A composition dealer cannot charge tax, an unregistered one has no
    GSTIN to claim against, and an SEZ unit's supply is an import; tax on
    their bills is listed in the warnings, not claimed.
    """
    settings = _settings()
    result = {
        "gstin": settings.gstin, "period": (start, end),
        "3.1a": _money(), "3.1b": _money(), "3.1c": _money(), "3.1e": _money(),
        "3.2": [], "4A5": _money(), "5": {"inter": ZERO, "intra": ZERO},
        "5_non_gst": {"inter": ZERO, "intra": ZERO},
        "warnings": [], "not_built": NOT_BUILT,
    }
    table = {"taxed": "3.1a", "zero_rated": "3.1b", "nil": "3.1c", "exempt": "3.1c",
             "non_gst": "3.1e"}
    unregistered = defaultdict(_money)
    for document in _invoices(start, end, settings.state):
        sign = -1 if document.is_note else 1
        for line in document.lines:
            _add(result[table[line.nature]], line, sign)
            if (line.nature == "taxed" and document.inter
                    and (not document.registered or document.registration == "composition")):
                _add(unregistered[document.place], line, sign)
    result["3.2"] = [
        {"place_of_supply": place, "taxable": totals["taxable"], "igst": totals["igst"]}
        for place, totals in sorted(unregistered.items())
    ]
    # Tax on advances for services is due when they are received, and
    # given back when they are adjusted: table 11 of GSTR-1, net, in 3.1(a).
    received, adjusted = _advances(start, end, settings.state)
    for totals, sign in ((received, 1), (adjusted, -1)):
        for money in totals.values():
            for column in money:
                result["3.1a"][column] += sign * money[column]

    for document in _bills(start, end, settings.state):
        sign = -1 if document.is_note else 1
        # A supply from an SEZ unit is an import in substance: its IGST is
        # paid on the bill of entry, not claimed off the unit's invoice.
        claimable = document.registered and document.registration == "regular"
        for line in document.lines:
            if line.nature in ("nil", "exempt"):
                result["5"]["inter" if document.inter else "intra"] += sign * line.taxable
            elif line.nature == "non_gst":
                result["5_non_gst"]["inter" if document.inter else "intra"] += sign * line.taxable
            elif claimable:
                _add(result["4A5"], line, sign)
            elif any(getattr(line, head) for head in HEADS):
                result["warnings"].append(
                    f"{document.number}: the supplier's registration is "
                    f"{document.registration or 'unknown'}, so tax on its bill is not "
                    "claimed as credit."
                )
    result["4A5"].pop("taxable")
    result["warnings"] = sorted(set(result["warnings"]))
    return result


def _gstn_date(value):
    return value.strftime("%d-%m-%Y")


def _gstn_items(items, heads=("iamt", "camt", "samt", "csamt")):
    names = {"iamt": "igst", "camt": "cgst", "samt": "sgst", "csamt": "cess"}
    return [
        {"num": index, "itm_det": {"rt": float(item["rate"]), "txval": float(item["taxable"])}
         | {key: float(item[names[key]]) for key in heads}}
        for index, item in enumerate(items, start=1)
    ]


def _gstn_advances(rows):
    by_place = defaultdict(list)
    for row in rows:
        by_place[(row["place_of_supply"], row["supply"])].append({
            "rt": float(row["rate"]), "ad_amt": float(row["taxable"]),
            "iamt": float(row["igst"]), "camt": float(row["cgst"]),
            "samt": float(row["sgst"]), "csamt": float(row["cess"]),
        })
    return [{"pos": place, "sply_ty": supply.upper(), "itms": items}
            for (place, supply), items in by_place.items()]


def gstr1_json(result):
    """
    GSTR-1 in the offline tool's JSON shape. Validate it in that tool
    before uploading: its schema is published by GSTN and revised by
    advisory, and this was written to the version known when it was.
    """
    start, _ = result["period"]
    b2b = defaultdict(list)
    for entry in result["b2b"]:
        b2b[entry["gstin"]].append({
            "inum": entry["number"], "idt": _gstn_date(entry["date"]),
            "val": float(entry["value"]), "pos": entry["place_of_supply"],
            "rchrg": "N",
            "inv_typ": {"regular": "R", "sez_with_payment": "SEWP",
                        "sez_without_payment": "SEWOP"}[entry["type"]],
            "itms": _gstn_items(entry["items"]),
        })
    b2cl = defaultdict(list)
    for entry in result["b2cl"]:
        b2cl[entry["place_of_supply"]].append({
            "inum": entry["number"], "idt": _gstn_date(entry["date"]),
            "val": float(entry["value"]),
            "itms": _gstn_items(entry["items"], heads=("iamt", "csamt")),
        })
    exp = defaultdict(list)
    for entry in result["exp"]:
        exp["WPAY" if entry["type"] == "with_payment" else "WOPAY"].append({
            "inum": entry["number"], "idt": _gstn_date(entry["date"]),
            "val": float(entry["value"]),
            "itms": _gstn_items(entry["items"], heads=("iamt", "csamt")),
        })
    cdnr = defaultdict(list)
    for entry in result["cdnr"]:
        cdnr[entry["gstin"]].append({
            "ntty": "C", "nt_num": entry["number"], "nt_dt": _gstn_date(entry["date"]),
            "val": float(entry["value"]), "pos": entry["place_of_supply"],
            "rchrg": "N", "inv_typ": "R", "itms": _gstn_items(entry["items"]),
        })
    return {
        "gstin": result["gstin"],
        "fp": start.strftime("%m%Y"),
        "b2b": [{"ctin": gstin, "inv": invoices} for gstin, invoices in b2b.items()],
        "b2cl": [{"pos": pos, "inv": invoices} for pos, invoices in b2cl.items()],
        "exp": [{"exp_typ": kind, "inv": invoices} for kind, invoices in exp.items()],
        "b2cs": [
            {"sply_ty": row["supply"].upper(), "pos": row["place_of_supply"], "typ": "OE",
             "rt": float(row["rate"]), "txval": float(row["taxable"]),
             "iamt": float(row["igst"]), "camt": float(row["cgst"]),
             "samt": float(row["sgst"]), "csamt": float(row["cess"])}
            for row in result["b2cs"]
        ],
        "cdnr": [{"ctin": gstin, "nt": notes} for gstin, notes in cdnr.items()],
        "at": _gstn_advances(result["advances_received"]),
        "txpd": _gstn_advances(result["advances_adjusted"]),
        "cdnur": [
            {"typ": {"b2cl": "B2CL", "exp_with_payment": "EXPWP",
                     "exp_without_payment": "EXPWOP"}[entry["type"]],
             "ntty": "C", "nt_num": entry["number"], "nt_dt": _gstn_date(entry["date"]),
             "val": float(entry["value"]), "pos": entry["place_of_supply"],
             "itms": _gstn_items(entry["items"], heads=("iamt", "csamt"))}
            for entry in result["cdnur"]
        ],
        "nil": {"inv": [
            {"sply_ty": {"inter_registered": "INTRB2B", "intra_registered": "INTRAB2B",
                         "inter_unregistered": "INTRB2C",
                         "intra_unregistered": "INTRAB2C"}[key],
             "nil_amt": float(totals["nil"]), "expt_amt": float(totals["exempt"]),
             "ngsup_amt": float(totals["non_gst"])}
            for key, totals in result["nil"].items()
        ]},
        "hsn": {
            table: [
                {"num": index, "hsn_sc": row["hsn"], "uqc": row["uqc"],
                 "qty": float(row["quantity"]), "rt": float(row["rate"]),
                 "txval": float(row["taxable"]), "iamt": float(row["igst"]),
                 "camt": float(row["cgst"]), "samt": float(row["sgst"]),
                 "csamt": float(row["cess"])}
                for index, row in enumerate(result[f"hsn_{table}"], start=1)
            ]
            for table in ("b2b", "b2c")
        },
        "doc_issue": {"doc_det": [
            {"doc_num": number, "docs": [{
                "num": 1, "from": series["from"], "to": series["to"],
                "totnum": series["total"], "cancel": series["cancelled"],
                "net_issue": series["total"] - series["cancelled"],
            }]}
            for number, series in enumerate(result["documents"], start=1)
        ]},
    }

