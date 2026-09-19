"""
Money arithmetic shared by every trading document.

These mixins live in Accounting because both Sales and Purchasing need
them and neither may import the other. Accounting already owns Tax and
compute_taxes, so it is the lowest layer that can express "a line with
a price, a discount and some taxes on it".
"""

from collections import defaultdict
from decimal import Decimal

from django.db import models

from apps.core.models import Company

from .models import compute_taxes, round_money


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

    def effective_taxes(self):
        """
        The taxes that actually apply, after the party's fiscal position
        substitutes or removes them. Without this the configuration sits
        there decorative: an export customer still gets charged VAT, and a
        reverse-charge vendor still has input tax claimed on their bill.
        """
        taxes = list(self.taxes.all())
        party = self.party_for_tax()
        if not taxes or party is None:
            return taxes
        profile = getattr(party, "tax_profile", None)
        if profile is None:
            return taxes
        return profile.applicable_taxes(taxes)

    def tax_amounts(self):
        """[(tax, amount)] for this line, honouring inclusive and compound taxes."""
        taxes = self.effective_taxes()
        if not taxes:
            return []
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
        return amounts

    def tax_total(self):
        return sum(
            (amount for amounts in self.line_tax_amounts().values() for _, amount in amounts),
            Decimal("0"),
        )

    def total(self):
        return self.subtotal() + self.tax_total()

    def tax_breakdown(self):
        """{tax: amount} across all lines, for invoice summary lines."""
        totals = defaultdict(Decimal)
        for amounts in self.line_tax_amounts().values():
            for tax, amount in amounts:
                totals[tax] += amount
        return dict(totals)
