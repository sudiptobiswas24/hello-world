"""
The bill of entry (L21): goods imported, cleared through customs.

The overseas supplier's bill is in its own currency at its own rate, and its payable stays there:
nothing here touches it. Customs value the same goods again, at the rate CBIC notified for the day
(the customs exchange rate, typed and kept), with the freight and insurance to the port added: the
assessable value. On it:

- basic customs duty, line by line at its rate;
- the social welfare surcharge, a share of that duty (Finance Act 2018, s.110);
- IGST on the assessable value plus the duty plus the surcharge (Customs Tariff Act s.3(7)).

Each line is rounded as the BE prints it: the assessable value to the paisa, each duty to the rupee
(Customs Act 1962, s.154A); the BE's totals are the sum of its lines.

Posting books what customs are owed as a bill to the customs authority, so it is paid, aged and
matched like any payable: Dr customs duty (the duty account), Dr input IGST, Cr the duty payable.
The duty and surcharge then land on the goods cleared, through land_on_receipts (by the BE's basis,
value by default): shelf, run or cost of sales, by where the goods are now. The IGST is input credit,
matched with GSTR-2B's IMPG table by port, number and date (apps/gst/gstr2b.py). The customs rate is
a valuation figure only: it never makes an exchange difference.

Corrections: a BE is voided, dated by correction_date: its landings released, its duty bill debited in
full. A re-assessment is a new BE that supersedes it; the old one is kept, marked superseded, never
edited. A BE whose IGST a filed GSTR-3B claimed is not voided.

Freight and insurance not known: Customs Valuation (Determination of Value of Imported Goods) Rules,
2007, rule 10(2), second proviso: freight at 20% of the FOB value, insurance at 1.125% of it (landing
charges were dropped from the value from 26 September 2017). Loaded as dated rows
(CustomsValuationDefault), unconfirmed until the customs broker confirms them.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounting.models import Account, round_money
from apps.core.models import (
    AuditModel, DocumentSequence, Party, correction_date, lock_rows, numbering_first, serialised, takes_back,
    to_date,
)

ZERO = Decimal("0")
HUNDRED = Decimal("100")
RUPEE = Decimal("1")


def to_the_rupee(amount):
    """Customs Act s.154A: duty, interest and the like to the nearest rupee."""
    return amount.quantize(RUPEE, rounding=ROUND_HALF_UP) + 0


class CustomsValuationDefault(AuditModel):
    """What freight and insurance are taken as when their actuals are not known, from a day."""

    valid_from = models.DateField(unique=True)
    freight_percent = models.DecimalField(max_digits=6, decimal_places=3, help_text="Of the FOB value.")
    insurance_percent = models.DecimalField(max_digits=6, decimal_places=3, help_text="Of the FOB value.")
    citation = models.CharField(max_length=255)
    confirmed = models.BooleanField(default=False, help_text="Confirmed by the customs broker or the accountant.")

    class Meta:
        ordering = ["-valid_from"]

    def __str__(self):
        return f"Freight {self.freight_percent}%, insurance {self.insurance_percent}% from {self.valid_from}"

    @classmethod
    def on(cls, day):
        row = cls.objects.filter(valid_from__lte=to_date(day)).order_by("-valid_from").first()
        if row is None:
            raise ValidationError(f"Nothing says what freight and insurance are taken as on {day}: give the "
                                  "actual figures.")
        return row


class EntryStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    POSTED = "posted", "Posted"
    VOID = "void", "Void"
    SUPERSEDED = "superseded", "Superseded"


STANDING = (EntryStatus.DRAFT, EntryStatus.POSTED)


class BillOfEntry(AuditModel):
    number = models.CharField(max_length=32, blank=True, editable=False)
    be_number = models.CharField(max_length=16, help_text="The bill of entry number as customs gave it.")
    be_date = models.DateField()
    port_code = models.CharField(max_length=6, help_text="The customs port code, six characters (INCCU1).")
    currency = models.ForeignKey("core.Currency", on_delete=models.PROTECT, related_name="+")
    customs_rate = models.DecimalField(
        max_digits=12, decimal_places=4,
        help_text="CBIC's notified exchange rate for imports on the day, as the BE states it.")
    freight = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text="Freight to the port, in the invoice currency; blank when not known (rule 10(2)).")
    insurance = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text="Insurance to the port, in the invoice currency; blank when not known (rule 10(2)).")
    freight_taken = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO, editable=False)
    insurance_taken = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO, editable=False)
    valuation_rule = models.CharField(max_length=255, blank=True, editable=False,
                                      help_text="Where freight or insurance were not known, what was taken.")
    bcd_rate = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal("7.5"),
                                   help_text="Basic customs duty, % — each line's unless it says otherwise.")
    sws_rate = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal("10"),
                                   help_text="Social welfare surcharge, % of the duty.")
    igst_rate = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal("18"))
    customs_party = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="+",
                                      help_text="Who the duty is paid to: the customs house, as a vendor.")
    payable_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+",
                                        help_text="Customs duty payable.")
    duty_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+",
                                     help_text="Where the duty sits until it lands on the goods.")
    igst_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+",
                                     help_text="Input IGST.")
    basis = models.CharField(max_length=10, default="value",
                             choices=[("value", "Value received"), ("quantity", "Quantity"),
                                      ("weight", "Weight in kilogrammes")],
                             help_text="What the duty is split over the goods by.")
    status = models.CharField(max_length=12, choices=EntryStatus.choices, default=EntryStatus.DRAFT, editable=False)
    duty_bill = models.OneToOneField("purchasing.Bill", null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="bill_of_entry", editable=False)
    void_note = models.OneToOneField("purchasing.Bill", null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+", editable=False)
    voided_on = models.DateField(null=True, blank=True, editable=False)
    void_reason = models.CharField(max_length=255, blank=True, editable=False)
    supersedes = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="superseded_by",
                                      help_text="The BE this re-assessment replaces.")

    class Meta:
        ordering = ["-be_date", "-id"]
        verbose_name_plural = "bills of entry"
        permissions = [("post_billofentry", "Can post or void a bill of entry")]
        constraints = [
            models.UniqueConstraint(fields=["number"], condition=~Q(number=""), name="bill_of_entry_number_unique"),
            models.CheckConstraint(check=Q(customs_rate__gt=0), name="bill_of_entry_rate_positive"),
        ]

    def __str__(self):
        return self.number or f"BE {self.be_number}"

    # -- figures ---------------------------------------------------------
    def lines_in_order(self):
        return list(self.lines.select_related("receipt_line__receipt", "receipt_line__order_line__item",
                                              "bill_line__bill").order_by("pk"))

    def fob(self):
        return sum((line.fob for line in self.lines.all()), ZERO)

    def totals(self):
        lines = list(self.lines.all())
        return {key: sum((getattr(line, key) for line in lines), ZERO)
                for key in ("fob", "assessable_value", "bcd", "sws", "igst")}

    def duty(self):
        totals = self.totals()
        return totals["bcd"] + totals["sws"]

    def receipts(self):
        return sorted({line.receipt_line.receipt for line in self.lines.select_related("receipt_line__receipt")},
                      key=lambda receipt: receipt.pk)

    def bills(self):
        return sorted({line.bill_line.bill for line in self.lines.select_related("bill_line__bill")},
                      key=lambda bill: bill.pk)

    def compute(self):
        """
        Freight and insurance (actual, or as rule 10(2) takes them), then each line: its share of the
        assessable value (total first, divided by FOB value, the last line taking what is left), its
        duty and surcharge to the rupee, and its IGST on all three to the rupee.
        """
        lines = self.lines_in_order()
        fob = sum((line.fob for line in lines), ZERO)
        rules = []
        if self.freight is None or self.insurance is None:
            default = CustomsValuationDefault.on(self.be_date)
            if self.freight is None:
                rules.append(f"freight {default.freight_percent.normalize():f}% of FOB")
            if self.insurance is None:
                rules.append(f"insurance {default.insurance_percent.normalize():f}% of FOB")
            rule = f"{', '.join(rules)} ({default.citation}{'' if default.confirmed else '; unconfirmed'})"
        self.freight_taken = (self.freight if self.freight is not None
                              else round_money(fob * default.freight_percent / HUNDRED))
        self.insurance_taken = (self.insurance if self.insurance is not None
                                else round_money(fob * default.insurance_percent / HUNDRED))
        self.valuation_rule = rule[:255] if rules else ""
        whole = round_money((fob + self.freight_taken + self.insurance_taken) * self.customs_rate)
        left = whole
        for index, line in enumerate(lines):
            last = index == len(lines) - 1
            line.assessable_value = left if last else round_money(whole * line.fob / fob) if fob else ZERO
            left -= line.assessable_value
            line.bcd = to_the_rupee(line.assessable_value * line.bcd_rate / HUNDRED)
            line.sws = to_the_rupee(line.bcd * line.sws_rate / HUNDRED)
            line.igst = to_the_rupee((line.assessable_value + line.bcd + line.sws) * line.igst_rate / HUNDRED)
            super(BillOfEntryLine, line).save(update_fields=["assessable_value", "bcd", "sws", "igst", "updated_at"])
        if self.pk:
            super().save(update_fields=["freight_taken", "insurance_taken", "valuation_rule", "updated_at"])

    # -- the document ----------------------------------------------------
    def save(self, *args, **kwargs):
        if not self._state.adding and not kwargs.get("update_fields"):
            stored = BillOfEntry.objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if stored and stored != EntryStatus.DRAFT:
                raise ValidationError(f"{self} is {stored}: a re-assessment is a new bill of entry that supersedes it.")
        self.port_code = (self.port_code or "").strip().upper()
        if len(self.port_code) != 6:
            raise ValidationError({"port_code": "A customs port code is six characters, INCCU1."})
        self.be_number = (self.be_number or "").strip()
        if not self.be_number.isdigit():
            raise ValidationError({"be_number": "A bill of entry number is its digits, as customs gave it."})
        if self.currency_id and self.currency.is_base:
            raise ValidationError({"currency": "A bill of entry values goods invoiced in a foreign currency."})
        if self.supersedes_id and self.supersedes.status != EntryStatus.POSTED:
            raise ValidationError({"supersedes": f"{self.supersedes} is {self.supersedes.status}: only a posted "
                                                 "bill of entry is re-assessed."})
        from .models import _require_vendor_role

        _require_vendor_role(self.customs_party)
        # One standing BE a number, port and day; a re-assessment carries its own number on.
        twin = BillOfEntry.objects.filter(port_code=self.port_code, be_number=self.be_number,
                                          be_date=to_date(self.be_date), status__in=STANDING).exclude(pk=self.pk)
        if self.supersedes_id:
            twin = twin.exclude(pk=self.supersedes_id)
        if twin.exists():
            raise ValidationError({"be_number": f"BE {self.be_number} of {to_date(self.be_date)} at {self.port_code} "
                                                f"is entered already, as {twin.first()}."})
        super().save(*args, **kwargs)
        if not kwargs.get("update_fields") and self.lines.exists():
            # A rate or a figure typed on the draft: its lines are worked again from it.
            self.lines.update(bcd_rate=self.bcd_rate, sws_rate=self.sws_rate, igst_rate=self.igst_rate)
            self.compute()

    def delete(self, *args, **kwargs):
        if self.status != EntryStatus.DRAFT:
            raise ValidationError(f"{self} is posted ({self.status}): void it instead.")
        self.lines.all().delete()
        return super().delete(*args, **kwargs)

    @serialised("status")
    def clear_receipt(self, receipt, bill):
        """Add the goods on `receipt`, as `bill` invoiced them, to this draft BE: one line a receipt line."""
        from .models import BillLine
        from apps.accounting.gst import charges_gst

        if self.status != EntryStatus.DRAFT:
            raise ValidationError(f"{self} is {self.status}.")
        lock_rows(receipt)
        if not receipt.posted or receipt.reverses_id:
            raise ValidationError(f"{receipt} is not a posted receipt of goods in.")
        if not bill.posted or bill.debits_id:
            raise ValidationError(f"{bill} is not a posted bill.")
        if bill.vendor_id != receipt.purchase_order.vendor_id:
            raise ValidationError(f"{bill} is {bill.vendor}'s and {receipt} came from {receipt.purchase_order.vendor}.")
        if charges_gst(bill.vendor) or bill.currency_id != self.currency_id:
            raise ValidationError(f"{bill} is not an overseas supplier's bill in {self.currency}: it is not imported.")
        added = 0
        for receipt_line in receipt.lines.select_related("order_line__item").order_by("pk"):
            bill_line = BillLine.objects.filter(bill=bill, order_line=receipt_line.order_line).first()
            if bill_line is None:
                continue
            taken = BillOfEntryLine.objects.filter(receipt_line=receipt_line, entry__status__in=STANDING).exclude(
                entry_id=self.supersedes_id).first()
            if taken is not None:
                raise ValidationError(f"{receipt} {receipt_line.order_line.item} is cleared on {taken.entry} already.")
            BillOfEntryLine.objects.create(
                entry=self, receipt_line=receipt_line, bill_line=bill_line,
                quantity=receipt_line.quantity_received,
                fob=round_money(receipt_line.quantity_received * bill_line.unit_price),
                bcd_rate=self.bcd_rate, sws_rate=self.sws_rate, igst_rate=self.igst_rate)
            added += 1
        if not added:
            raise ValidationError(f"Nothing on {receipt} is billed on {bill}.")
        self.compute()
        return self

    def locked_before_it(self):
        """What it re-assesses is held before it, by key order: the old BE was entered first."""
        return [BillOfEntry(pk=self.supersedes_id)] if self.supersedes_id else []

    @transaction.atomic
    @serialised("status", held_first=True)
    def post(self, by=None):
        """Book what customs are owed, land the duty on the goods, and supersede what it re-assesses."""
        from .models import Bill, BillLine

        if self.status != EntryStatus.DRAFT:
            raise ValidationError(f"{self} is {self.status}.")
        numbering_first()
        lines = self.lines_in_order()
        if not lines:
            raise ValidationError("Clear the goods on a receipt first: a bill of entry with no goods values nothing.")
        if self.be_date > timezone.localdate():
            raise ValidationError({"be_date": "That day has not come."})
        taken = BillOfEntryLine.objects.filter(receipt_line__in=[line.receipt_line for line in lines],
                                               entry__status=EntryStatus.POSTED).exclude(
            entry__in=[self.pk, self.supersedes_id]).first()
        if taken is not None:
            raise ValidationError(f"{taken.receipt_line.receipt} is cleared on {taken.entry} already.")
        self.compute()
        totals = self.totals()
        if totals["bcd"] + totals["sws"] + totals["igst"] <= 0:
            raise ValidationError("This bill of entry assesses no duty and no IGST.")
        self.number = DocumentSequence.next_for("purchasing.bill_of_entry", self.be_date,
                                                name="Bills of entry", prefix="BOE-")
        bill = Bill.objects.create(
            created_by=by, updated_by=by, vendor=self.customs_party, bill_date=self.be_date,
            reference=self._bill_reference(), payable_account=self.payable_account)
        duty = totals["bcd"] + totals["sws"]
        if duty:
            BillLine.objects.create(
                bill=bill, quantity=Decimal("1"), unit_price=duty, expense_account=self.duty_account,
                description=f"Basic customs duty {totals['bcd']} and social welfare surcharge {totals['sws']}, "
                            f"BE {self.be_number} of {self.be_date:%d.%m.%Y} at {self.port_code}"[:255])
        if totals["igst"]:
            BillLine.objects.create(
                bill=bill, quantity=Decimal("1"), unit_price=totals["igst"], expense_account=self.igst_account,
                description=f"IGST paid on import, BE {self.be_number} of {self.be_date:%d.%m.%Y} "
                            f"at {self.port_code}"[:255])
        bill.post(memo=f"Bill of entry {self.be_number} ({self.number})")
        self.duty_bill = bill
        self.status = EntryStatus.POSTED
        super().save(update_fields=["number", "duty_bill", "status", "updated_at"])
        if self.supersedes_id:
            # What it re-assesses, before its own duty lands: the old landings released and the old bill
            # debited, so the goods never carry both. After this BE's bill: bills before shelves (lock order).
            self.supersedes.void(on_date=self.be_date, reason=f"Re-assessed on BE {self.be_number}",
                                 superseded=True)
        if duty:
            from .models import land_on_receipts

            land_on_receipts([line.receipt_line for line in lines], duty, self.basis, self.be_date,
                             reference=self.number, account=self.duty_account, source={"bill_of_entry": self})
        return self

    def _bill_reference(self):
        """The duty bill's reference: the BE's number, and which re-assessment of it (one invoice number a year)."""
        depth, earlier = 0, self.supersedes
        while earlier is not None:
            depth, earlier = depth + 1, earlier.supersedes
        return (f"BE {self.be_number}" + (f" re-assessment {depth}" if depth else ""))[:64]

    @transaction.atomic
    @serialised("status")
    @takes_back
    def void(self, on_date=None, reason="", superseded=False):
        """Release its landings and debit its duty bill in full, dated by correction_date."""
        from apps.accounting.gst import refuse_in_a_filed_return

        if self.status != EntryStatus.POSTED:
            raise ValidationError(f"{self} is {self.status}.")
        if not (reason or "").strip():
            raise ValidationError({"reason": "Say why the bill of entry is voided."})
        day = correction_date(on_date, self.be_date, f"{self} is not voided on", "it was assessed")
        refuse_in_a_filed_return(self.be_date, f"{self} is not voided")
        bill = self.duty_bill
        lock_rows(bill)
        # The bill first, then the shelves its duty went onto: bill lines before stock (lock order).
        self.void_note = bill.create_debit_note(memo=f"Bill of entry {self.be_number} voided: {reason}"[:255],
                                                on_date=day)
        for landing in self.landings.filter(released_entry__isnull=True).order_by("pk"):
            landing.release(on_date=day)
        self.status = EntryStatus.SUPERSEDED if superseded else EntryStatus.VOID
        self.voided_on = day
        self.void_reason = reason.strip()[:255]
        super().save(update_fields=["status", "void_note", "voided_on", "void_reason", "updated_at"])
        return self


class BillOfEntryLine(AuditModel):
    entry = models.ForeignKey(BillOfEntry, on_delete=models.CASCADE, related_name="lines")
    receipt_line = models.ForeignKey("purchasing.GoodsReceiptLine", on_delete=models.PROTECT, related_name="+")
    bill_line = models.ForeignKey("purchasing.BillLine", on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=18, decimal_places=4, editable=False)
    fob = models.DecimalField(max_digits=18, decimal_places=2, editable=False,
                              help_text="The invoice value of the goods, in the invoice currency.")
    assessable_value = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO, editable=False)
    bcd_rate = models.DecimalField(max_digits=6, decimal_places=3)
    bcd = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO, editable=False)
    sws_rate = models.DecimalField(max_digits=6, decimal_places=3)
    sws = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO, editable=False)
    igst_rate = models.DecimalField(max_digits=6, decimal_places=3)
    igst = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO, editable=False)

    class Meta:
        ordering = ["entry", "id"]
        constraints = [
            models.CheckConstraint(check=Q(bcd_rate__gte=0) & Q(sws_rate__gte=0) & Q(igst_rate__gte=0),
                                   name="bill_of_entry_rates_not_negative"),
            models.CheckConstraint(check=Q(quantity__gt=0), name="bill_of_entry_quantity_positive"),
        ]

    def __str__(self):
        return f"{self.entry} {self.receipt_line.order_line.item}"

    def locked_before_it(self):
        from apps.core.models import documents_it_answers_to

        return documents_it_answers_to(self, "entry")

    @staticmethod
    def _fixed(entry):
        return entry.status != EntryStatus.DRAFT

    @transaction.atomic
    def save(self, *args, **kwargs):
        from apps.core.models import answer_to_its_document

        answer_to_its_document(self, "entry", lambda entry: f"{entry} is posted; a re-assessment is a new "
                                                             "bill of entry.", fixed=self._fixed)
        super().save(*args, **kwargs)
        BillOfEntry.objects.get(pk=self.entry_id).compute()

    @transaction.atomic
    def delete(self, *args, **kwargs):
        from apps.core.models import answer_to_its_document

        answer_to_its_document(self, "entry", lambda entry: f"{entry} is posted; void it instead.", fixed=self._fixed)
        entry = BillOfEntry.objects.get(pk=self.entry_id)
        result = super().delete(*args, **kwargs)
        if entry.lines.exists():
            entry.compute()
        return result


def standing_entries(start, end):
    """Posted bills of entry dated in [start, end]: what GSTR-2B's IMPG table is matched with."""
    return BillOfEntry.objects.filter(status=EntryStatus.POSTED, be_date__gte=start, be_date__lte=end)
