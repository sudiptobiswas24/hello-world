"""
Money arithmetic shared by every trading document.

These mixins live in Accounting because both Sales and Purchasing need
them and neither may import the other. Accounting already owns Tax and
compute_taxes, so it is the lowest layer that can express "a line with
a price, a discount and some taxes on it".
"""

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import Company

from .models import Tax, compute_taxes, round_money


def refuse_included(taxes):
    """
    A tax included in the price is supported by the tax engine but by no
    document: every document books its lines' net amount as revenue or
    cost and adds the tax on top, so an inclusive 1,180 was billed at
    1,360 with 180 of tax left inside revenue. Refused where a document
    first computes its tax, until documents extract the base.
    """
    for tax in taxes:
        if tax.price_included:
            raise ValidationError(
                f"{tax.code} is set as included in the price, which documents "
                "do not support: the line's amount would be booked with the "
                "tax still inside it and the tax charged again on top."
            )


def _currency_or_base(currency_id):
    """A document with no currency is in the company's own."""
    if currency_id is not None:
        return currency_id
    base = Company.get().currency()
    return base.pk if base is not None else None


def refuse_naming_another_order_line(line, document, party, order):
    """
    Shared rule B, the order-line half (apps.core.models.answer_to_its_document
    is the other): an invoice or bill line bills only an order line of its own
    document's party and currency, on its own document's order where the
    document names one, for the item or charge that line ordered.

    The delivery and the receipt line had this and the invoice and bill line
    did not: Acme's line read invoiced while another customer was billed, a
    euro bill on a dollar order posted 4,450 as exchange loss, and a bill for
    one widget cleared another's accrual. One function for both sides, so a
    fix to either is a fix to both (CLAUDE.md, mistake 5).

    `party` is the field the document and the order both name it by
    ("customer", "vendor"); `order` the one the document names its order by
    ("sales_order", "purchase_order"). A line naming neither item nor charge
    takes the order line's. Asked when the line is saved and again, under the
    order's lock, when the document posts: the document's party or order may
    have changed since.
    """
    if not line.order_line_id:
        return
    order_line = line.order_line
    placed = order_line.order
    named = getattr(document, f"{order}_id")
    label = order_line.label()
    if named and named != placed.pk:
        raise ValidationError({"order_line": [
            f"{label} is on {placed}; {document} bills {getattr(document, order)}."]})
    if getattr(placed, f"{party}_id") != getattr(document, f"{party}_id"):
        raise ValidationError({"order_line": [
            f"{label} is on {getattr(placed, party)}'s order {placed}; {document} is for "
            f"{getattr(document, party)}."]})
    if _currency_or_base(placed.currency_id) != _currency_or_base(document.currency_id):
        raise ValidationError({"order_line": [
            f"{label} is on {placed}, in {placed.currency}; {document} is in {document.currency}."]})
    if line.item_id is None and line.charge_id is None:
        line.item_id, line.charge_id = order_line.item_id, order_line.charge_id
    if (line.item_id, line.charge_id) != (order_line.item_id, order_line.charge_id):
        raise ValidationError({"order_line": [
            f"{placed} ordered {order_line.item or order_line.charge} on that line; this line bills "
            f"{line.item or line.charge}."]})


class TaxedLineMixin(models.Model):
    """
    Money arithmetic for one line of a trading document: gross, discount,
    net, tax. Taxes apply to the discounted net, which is the conventional
    order.

    Lives here rather than in Sales because Purchasing needs exactly the
    same arithmetic, and a second implementation of it is how the two
    sides drift into charging tax differently on the way in and on the way
    out.
    """

    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit_price = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text="On a sales document, left blank it resolves from the price list "
                  "or the item's list price.",
    )
    discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0"),
        help_text="Line discount, e.g. 10.00 for 10% off.",
    )

    class Meta:
        abstract = True

    def gross_amount(self):
        return round_money(self.quantity * self.unit_price)

    def discount_amount(self):
        return round_money(self.gross_amount() * self.discount_percent / Decimal("100"))

    def net_amount(self):
        """Amount before tax, after discount — the invoice line 'subtotal'."""
        return self.gross_amount() - self.discount_amount()

    # Kept as the conventional name for the pre-tax line amount.
    def subtotal(self):
        return self.net_amount()

    def party_for_tax(self):
        """
        The party whose fiscal position governs this line — the customer on
        a sales document, the vendor on a purchase one. Set by subclasses.
        """
        return None

    def fixed_tax_amounts(self):
        """
        [(tax, amount)] this line is bound to rather than computes, or None
        when it computes. Only a line that posts is ever bound; see
        PostedLineMixin.
        """
        return None

    def effective_taxes(self):
        """
        The taxes that actually apply, after the party's fiscal position
        substitutes or removes them. Without this the configuration sits
        there decorative: an export customer still gets charged VAT, and a
        reverse-charge vendor still has input tax claimed on their bill.
        """
        fixed = self.fixed_tax_amounts()
        if fixed is not None:
            return [tax for tax, _ in fixed]
        taxes = list(self.taxes.all())
        party = self.party_for_tax()
        if not taxes or party is None:
            return taxes
        profile = getattr(party, "tax_profile", None)
        if profile is None:
            from .gst import GstSettings

            if GstSettings.active() is not None:
                # Under GST a party nobody has placed cannot be taxed:
                # whether the supply bears central and state tax or
                # integrated tax depends on where it is. Charging the
                # intra-state pair by default is the plausible wrong
                # answer that takes months to unwind.
                raise ValidationError(
                    f"{party} has no tax profile, so there is no knowing "
                    "which state it is in — and a supply inside the state "
                    "and one across a state line bear different taxes."
                )
            return taxes
        return profile.applicable_taxes(taxes, place=self.place_for(profile))

    def place_for(self, profile):
        """
        Where this line's supply is: its document says, when it moves goods
        to a buyer (a sale, see gst.place_of_supply); otherwise the party's
        state on record.
        """
        return profile.place_of_supply()

    def tax_amounts(self):
        """[(tax, amount)] for this line, honouring inclusive and compound taxes."""
        fixed = self.fixed_tax_amounts()
        if fixed is not None:
            return fixed
        taxes = self.effective_taxes()
        if not taxes:
            return []
        refuse_included(taxes)
        _, lines, _ = compute_taxes(taxes, self.net_amount(), self.quantity)
        return lines

    def tax_total(self):
        return sum((amount for _, amount in self.tax_amounts()), Decimal("0"))

    def total(self):
        return self.net_amount() + self.tax_total()

    def is_charge(self):
        """True for a line billing a service charge rather than an item."""
        return getattr(self, "charge_id", None) is not None

    def label(self):
        """What this line is called on a document."""
        return (
            getattr(self, "description", "")
            or (str(self.charge) if self.is_charge() else "")
            or (str(self.item) if getattr(self, "item_id", None) else "")
            or "—"
        )


class TaxedDocumentMixin(models.Model):
    """Totals for a document made of TaxedLineMixin lines."""

    class Meta:
        abstract = True

    def subtotal(self):
        return sum((line.net_amount() for line in self.lines.all()), Decimal("0"))

    def line_tax_amounts(self):
        """
        {line: [(tax, amount)]} for every line, under the company's tax
        rounding rule.

        Rounding per line and rounding per document differ by pennies —
        three lines of 33.33 at 20% give 20.01 one way and 20.00 the
        other — and which is correct is a jurisdiction's choice, not a
        preference. Getting it wrong fails a VAT return's reconciliation
        by an amount too small to find and too persistent to ignore.

        Under document rounding the difference is pushed back onto the
        largest line rather than left floating, so the document's tax
        still equals the sum of its lines' tax. Anything else leaves
        every downstream report — the ledger, revenue by item, the
        statement — disagreeing with the invoice by a cent.
        """
        lines = list(self.lines.all())
        if Company.get().tax_rounding != "document":
            return {line: line.tax_amounts() for line in lines}

        # A line bound to what was posted is not re-rounded with the rest:
        # re-allocating its pennies is recomputing a fact.
        fixed = {line: line.fixed_tax_amounts() for line in lines}
        lines = [line for line in lines if fixed[line] is None]

        # Group by the exact set of taxes that applies, because compound
        # and price-included taxes depend on what else is on the line.
        groups = defaultdict(list)
        for line in lines:
            taxes = tuple(sorted(line.effective_taxes(), key=lambda tax: (tax.sequence, tax.code)))
            groups[taxes].append(line)

        amounts = {line: [] for line in lines}
        for taxes, group in groups.items():
            if not taxes:
                continue
            refuse_included(taxes)
            net = sum((line.net_amount() for line in group), Decimal("0"))
            quantity = sum((line.quantity for line in group), Decimal("0"))
            _, totals, _ = compute_taxes(list(taxes), net, quantity)
            biggest = max(group, key=lambda line: (line.net_amount(), line.pk or 0))
            for tax, target in totals:
                allocated = Decimal("0")
                for line in group:
                    if line is biggest:
                        continue
                    share = (line.net_amount() / net) if net else Decimal("0")
                    part = round_money(target * share)
                    allocated += part
                    amounts[line].append((tax, part))
                amounts[biggest].append((tax, target - allocated))
        amounts.update({line: bound for line, bound in fixed.items() if bound is not None})
        return amounts

    def tax_total(self):
        # Tax the company pays itself under reverse charge is not part of
        # what the other party is owed.
        return sum(
            (amount for amounts in self.line_tax_amounts().values() for tax, amount in amounts
             if not tax.reverse_charge),
            Decimal("0"),
        )

    def total(self):
        return self.subtotal() + self.tax_total()

    def worth_within(self, limit):
        """
        What the document can still come to: its total, less each line's part beyond
        `limit(line)` (what a line closed short will never bill), at that line's own share
        of the tax the total counts.
        """
        worth = self.total()
        for line, amounts in self.line_tax_amounts().items():
            short = line.quantity - limit(line)
            if short > 0 and line.quantity:
                gross = line.net_amount() + sum(
                    (amount for tax, amount in amounts if not tax.reverse_charge), Decimal("0"))
                worth -= round_money(gross * short / line.quantity)
        return worth

    def tax_breakdown(self):
        """{tax: amount} across all lines, for invoice summary lines."""
        totals = defaultdict(Decimal)
        for amounts in self.line_tax_amounts().values():
            for tax, amount in amounts:
                totals[tax] += amount
        return dict(totals)


class PostedLineMixin(models.Model):
    """
    A line of a document that posts to the ledger: an invoice line, a
    bill line.

    What such a line bore in tax is a fact once it posts, recorded in its
    `recorded_taxes` and never recomputed. Recomputing it reads today's
    configuration: the party moved state, took an export position, or
    GST was switched on, and a posted invoice would quietly show taxes it
    never charged, disagreeing with its own journal entry and with the
    return it was filed in.

    A correcting line (a credit note's, a debit note's) is bound to the
    line it corrects: the same taxes, in proportion to how much of the
    line's value it gives back, the last of it taking whatever is left so
    that what is reversed never differs from what was charged by a penny.
    "The last of it" is measured in value, not quantity: a price
    adjustment credits every unit at a fraction of the price, and by
    quantity would reverse the whole tax.
    Before this, a credit note re-derived its taxes from the party as it
    stood, so a customer who moved state between invoice and credit note
    had central and state tax charged and integrated tax reversed.
    """

    hsn_code = models.CharField(
        max_length=8, blank=True, editable=False,
        help_text="HSN or SAC as it stood when the line posted — the code "
                  "its return reports, whatever the item says later.",
    )

    posted_net = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True, editable=False,
        help_text="The line's amount before tax when it posted. With the taxes "
                  "it recorded, what reports sum instead of working every line "
                  "out again.",
    )

    class Meta:
        abstract = True

    def document(self):
        raise NotImplementedError

    def corrected_line(self):
        """The line this one corrects, on a credit or debit note."""
        return None

    def corrections(self):
        """Posted lines correcting this one."""
        raise NotImplementedError

    def current_hsn(self):
        corrected = self.corrected_line()
        if corrected is not None and corrected.document().taxes_recorded:
            return corrected.hsn_code
        if getattr(self, "item_id", None):
            return self.item.hsn_code
        if getattr(self, "charge_id", None):
            return self.charge.hsn_code
        return ""

    def fixed_tax_amounts(self):
        if self.document().taxes_recorded:
            return [(row.tax, row.amount) for row in self.recorded_taxes.all()]
        original = self.corrected_line()
        if original is None or not original.document().taxes_recorded:
            return None
        rows = list(original.recorded_taxes.all())
        others = [line for line in original.corrections() if line.pk != self.pk]
        net = self.net_amount()
        whole = original.net_amount()
        if net >= whole - sum(
            (line.net_amount() for line in others), Decimal("0")
        ):
            taken = defaultdict(Decimal)
            for line in others:
                for tax, amount in line.tax_amounts():
                    taken[tax.pk] += amount
            return [(row.tax, row.amount - taken[row.tax_id]) for row in rows]
        return [
            (row.tax, round_money(row.amount * net / whole) if whole else row.amount)
            for row in rows
        ]


class RecordedLineTax(models.Model):
    """
    One tax as a posted line bore it: the rate and the return column
    frozen with it, because a rate is changed by notification and a
    column by whoever edits the tax next.
    """

    tax = models.ForeignKey(Tax, on_delete=models.PROTECT, related_name="+")
    rate = models.DecimalField(max_digits=9, decimal_places=4)
    gst_head = models.CharField(max_length=8, blank=True)
    reverse_charge = models.BooleanField(
        default=False, help_text="Paid by the company, not charged by the vendor, as the tax stood.")
    taxable = models.DecimalField(
        max_digits=18, decimal_places=2,
        help_text="The line's net amount the tax was charged on, in the "
                  "document's currency.",
    )
    amount = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        abstract = True
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__gte=0, taxable__gte=0),
                name="%(app_label)s_%(class)s_not_negative",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError(
                "What a posted line bore in tax is a fact. Correct it with a "
                "credit or debit note."
            )
        super().save(*args, **kwargs)


class PostedTaxDocumentMixin(models.Model):
    """
    The tax facts a document freezes when it posts: each line's taxes,
    and the party's registration and state as they stood.

    A correcting note takes its original's registration and state, not
    the party's current ones: under GST a credit note is reported
    against the invoice it corrects, and a customer's re-registration
    does not move an old sale.
    """

    taxes_recorded = models.BooleanField(
        default=False, editable=False,
        help_text="Set when posting wrote down what each line bore. A "
                  "document posted before that existed computes, as it "
                  "always did.",
    )
    party_gstin = models.CharField(max_length=15, blank=True, editable=False)
    party_registration = models.CharField(
        max_length=16, blank=True, editable=False,
        help_text="Regular, SEZ, overseas… as it stood: which table of a "
                  "return the document is filed in.",
    )
    place_of_supply = models.CharField(max_length=2, blank=True, editable=False)

    class Meta:
        abstract = True

    def tax_party(self):
        raise NotImplementedError

    def corrected_document(self):
        return None

    def place_for(self, profile):
        """Where the supply is (gst.place_of_supply): a sale that moves goods says; else the party's state."""
        return profile.place_of_supply()

    def record_taxes(self):
        """
        Write down what every line bore. Called last in posting, after the
        journal entry is built from the same figures: a posting that fails
        before this point leaves nothing that claims to be recorded.
        """
        from .gst import GstSettings

        under_gst = GstSettings.active() is not None
        original = self.corrected_document()
        if original is not None and original.taxes_recorded:
            self.party_gstin = original.party_gstin
            self.party_registration = original.party_registration
            self.place_of_supply = original.place_of_supply
        else:
            profile = getattr(self.tax_party(), "tax_profile", None)
            self.party_gstin = profile.gstin if profile else ""
            self.party_registration = profile.gst_registration if profile else ""
            # The place its lines were taxed at: one rule for both.
            self.place_of_supply = (self.place_for(profile) or "") if profile else ""

        for line, amounts in self.line_tax_amounts().items():
            for tax, amount in amounts:
                if under_gst and not tax.gst_head:
                    raise ValidationError(
                        f"Tax {tax.code} has no GST head, so no return can say "
                        "which column it belongs in. Set it before posting."
                    )
                line.recorded_taxes.create(
                    tax=tax, rate=tax.rate, gst_head=tax.gst_head, reverse_charge=tax.reverse_charge,
                    taxable=line.net_amount(), amount=amount,
                )
            line.hsn_code = line.current_hsn()
            line.posted_net = line.net_amount()
            type(line).objects.filter(pk=line.pk).update(
                hsn_code=line.hsn_code, posted_net=line.posted_net)
        self.taxes_recorded = True
