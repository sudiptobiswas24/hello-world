"""
Money arithmetic shared by every trading document.

These mixins live in Accounting because both Sales and Purchasing need
them and neither may import the other. Accounting already owns Tax and
compute_taxes, so it is the lowest layer that can express "a line with
a price, a discount and some taxes on it".
"""

from collections import defaultdict
from decimal import ROUND_DOWN, Decimal

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


def refuse_correcting_a_note(document, original):
    """
    A note corrects an invoice or a bill, never another note. Crediting the
    credit note of 500 on an invoice of 1,000 with a second note of 500 took
    receivables to 0 with the goods still out (O170). `original` is the
    note's pointer ("credits", "debits"). Asked of the note in save() and
    of each of its lines, so the posting asks it again.
    """
    corrected = getattr(document, original) if getattr(document, f"{original}_id") else None
    if corrected is not None and getattr(corrected, f"{original}_id"):
        raise ValidationError({original: [
            f"{corrected} is itself a note, correcting {getattr(corrected, original)}; "
            f"correct {getattr(corrected, original)} instead."]})


def refuse_correcting_another_line(line, document, corrects, original, fill=False):
    """
    Shared rule B, the correcting half: a credit or debit note's line gives
    back only a line of the document the note corrects, posted, in the
    note's currency, and for the same order line where it names one.

    Asked of the order line alone, another customer's invoice named Acme's
    invoice line as the one it credited: Acme's line then had nothing left
    to credit. `corrects` is the line's pointer ("credits_line",
    "debits_line"), `original` the note's ("credits", "debits"); the party
    is the note's own check (a note is for its original's party). Asked
    when the line is saved and again when the note posts, under the
    original's lock (refuse_correcting_past_what_it_holds asks how much).
    """
    if not getattr(line, f"{corrects}_id"):
        refuse_an_account_its_original_never_used(line, document, original)
        return
    corrected = getattr(line, corrects)
    held = corrected.document()
    refuse_correcting_a_note(held, original)
    # A claim gives back money on the line, not goods: its line names no order line (quantity_invoiced).
    claim = bool(getattr(document, "claim_reason", ""))
    if fill and not claim and not line.order_line_id:
        # The order line of the line it gives back: left blank, the order
        # line read invoiced 10 after the whole 10 were credited (O172).
        line.order_line_id = corrected.order_line_id
    noted = getattr(document, f"{original}_id")
    if noted != held.pk:
        raise ValidationError({corrects: [
            f"{corrected.label()} is on {held}; {document} corrects "
            f"{getattr(document, original) if noted else 'no document'}."]})
    if not held.posted:
        raise ValidationError({corrects: [f"{held} has not posted; there is nothing on it to give back."]})
    if _currency_or_base(held.currency_id) != _currency_or_base(document.currency_id):
        raise ValidationError({corrects: [f"{held} is in {held.currency}; {document} is in {document.currency}."]})
    if (line.order_line_id or not claim) and line.order_line_id != corrected.order_line_id:
        raise ValidationError({"order_line": [
            f"{corrected.label()} billed {corrected.order_line or 'no order line'}; this line names "
            f"{line.order_line}."]})
    # And through the account its line was booked to (O201): a typed credit
    # line on a sale booked to 4000 credited 4100, and one revenue account
    # read low by what the other read high, for good. Left blank, it takes
    # the line's (on save, `fill`).
    account = next(name for name in ("revenue_account", "expense_account")
                   if hasattr(type(line), name))
    booked = getattr(corrected, f"{account}_id")
    if fill and booked and not getattr(line, f"{account}_id"):
        setattr(line, f"{account}_id", booked)
    if booked and getattr(line, f"{account}_id") != booked:
        raise ValidationError({account: [
            f"{corrected.label()} on {held} was booked to {getattr(corrected, account)}; a line giving it "
            f"back is booked there too, not to {getattr(line, account)}."]})


def refuse_an_account_its_original_never_used(line, document, original):
    """
    A note's line that names no line of its original (a goodwill credit typed
    in, a price variation) is booked to an account the original booked a line
    to (O201, review F6): "Goodwill" 100 credited to 4100 on a sale booked to
    4000 left 4000 at -1,000 and 4100 at +100. A note line naming no account
    is left to the line's own default, which save() then asks here again.
    """
    if not getattr(document, f"{original}_id"):
        return
    account = next(name for name in ("revenue_account", "expense_account") if hasattr(type(line), name))
    chosen = getattr(line, f"{account}_id")
    if not chosen:
        return
    held = getattr(document, original)
    booked = {getattr(row, f"{account}_id") for row in held.lines.all()} - {None}
    if booked and chosen not in booked:
        from apps.accounting.models import Account

        names = ", ".join(str(account_) for account_ in Account.objects.filter(pk__in=booked).order_by("code"))
        raise ValidationError({account: [
            f"{held} booked its lines to {names}; a note on it is booked there too, not to {getattr(line, account)}."]})


def line_value(line):
    """A line's value before tax, unrounded: what partial notes on it add up to exactly."""
    return line.quantity * (line.unit_price or Decimal("0")) * (1 - (line.discount_percent or 0) / Decimal("100"))


def value_left(corrected, corrects, already, on):
    """
    What is left to give back of `corrected`, an invoice or bill line: its
    value (unrounded, every posted note on it counted, a claim's money
    included) and its quantity (`already(line)`, goods notes only).
    """
    notes = type(corrected)._base_manager.filter(**{corrects: corrected, f"{on}__posted": True})
    value = line_value(corrected) - sum((line_value(note) for note in notes), Decimal("0"))
    return value, corrected.quantity - already(corrected)


def booked_left(corrected, corrects, on):
    """What is left of `corrected` as the documents booked it: its net less every posted note line's on it."""
    notes = type(corrected)._base_manager.filter(**{corrects: corrected, f"{on}__posted": True})
    return corrected.net_amount() - sum((note.net_amount() for note in notes), Decimal("0"))


def _lines_booking(target, quantity):
    """Note lines, 2-place prices, of `quantity` whose booked nets add to `target` exactly."""
    cent = Decimal("0.01")
    price = (target / quantity).quantize(cent, rounding=ROUND_DOWN) if quantity else Decimal("0")
    if quantity <= 1:
        # A step of quantity x 0.01 is under a paisa, so one of two prices books it.
        for candidate in (price, price + cent):
            if round_money(quantity * candidate) == target:
                return [(quantity, candidate, Decimal("0"))]
        return [(quantity, price, Decimal("0"))]
    if round_money(quantity * price) == target:
        return [(quantity, price, Decimal("0"))]
    first = round_money((quantity - 1) * price)
    return [(quantity - 1, price, Decimal("0")), (Decimal("1"), target - first, Decimal("0"))]


def priced_to_give_back(corrected, quantity, corrects, already, on):
    """
    The note lines, as (quantity, unit price, discount), that give back
    `quantity` of `corrected`: at its own price, or at the goods' share of
    what is left of it when a claim has given back money on it (O185):
    10 x 100 with a claim of 100 gives back 900 for the ten, and the claim
    keeps what it gave. The share is a total divided last; when the paisa
    does not divide it, one unit takes what is left, so the whole comes back.

    What the rule allows, in words (O199): a part of what is left is given
    back at its share of the value left, unrounded, and may book up to a
    paisa under it; the last of a line is given back at what is left of it
    as the documents booked it, to the paisa, so the line's notes add up to
    what it booked. 2.5 at 3.33 books 8.33; two notes of 1.25 booked 4.16
    each and left a paisa on the receivable, where the second now books
    4.17 (0.25 at 3.33 and 1 at 3.34).
    """
    left, held = value_left(corrected, corrects, already, on)
    if quantity == held and held > 0:
        target = booked_left(corrected, corrects, on)
        own = type(corrected)(quantity=quantity, unit_price=corrected.unit_price or Decimal("0"),
                              discount_percent=corrected.discount_percent or Decimal("0"))
        if own.net_amount() == target:
            return [(quantity, corrected.unit_price, corrected.discount_percent)]
        return _lines_booking(max(target, Decimal("0")), quantity)
    share = left if quantity == held else (left * quantity / held if held > 0 else Decimal("0"))
    if share >= quantity * (corrected.unit_price or Decimal("0")) * (1 - corrected.discount_percent / Decimal("100")):
        return [(quantity, corrected.unit_price, corrected.discount_percent)]
    share = max(share, Decimal("0"))
    price = (share / quantity).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    if quantity * price == share or quantity <= 1:
        return [(quantity, price, Decimal("0"))]
    rest = (share - (quantity - 1) * price).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    return [(quantity - 1, price, Decimal("0")), (Decimal("1"), rest, Decimal("0"))]


def refuse_correcting_past_what_it_holds(lines, corrects, already, on, by_value_only=False):
    """
    A note gives back no more of a line than it holds: `already(line)` is
    what posted notes have given back of it, by quantity, and two lines of
    this note on one line count together. A claim (`by_value_only`) gives
    back money, not goods: it is held to the value left alone.

    And no more than it is worth, per unit (O171, O185): by quantity alone,
    ten at 1,000 given back on a line of ten at 100 posted, receivables
    -9,000; by the line's total value alone, one at 1,000 posted and the
    other nine could not be given back. So a note's value on a line is held
    to its quantity's share of the value left: every posted note on the line
    counted, a claim's money included, unrounded, so three notes of one on a
    line of three add up to it exactly. `on` is the note line's document
    field ("invoice", "bill").
    """
    from apps.core.api import plain

    going = defaultdict(Decimal)
    worth = defaultdict(Decimal)
    booked = defaultdict(Decimal)
    held = {}
    for line in lines:
        corrected = getattr(line, corrects) if getattr(line, f"{corrects}_id") else None
        if corrected is None:
            continue
        held[corrected.pk] = corrected
        going[corrected.pk] += line.quantity
        worth[corrected.pk] += line_value(line)
        booked[corrected.pk] += line.net_amount()
        if by_value_only:
            continue
        given = already(corrected)
        if given + going[corrected.pk] > corrected.quantity:
            raise ValidationError({corrects: [
                f"Only {plain(max(corrected.quantity - given, Decimal('0')))} of {corrected.label()} on "
                f"{corrected.document()} is left to give back; this note gives back {plain(going[corrected.pk])}."]})
    for pk, corrected in held.items():
        left, quantity = value_left(corrected, corrects, already, on)
        if not by_value_only and going[pk] == quantity and quantity > 0:
            # The last of the line: held to what is left of it as booked, to the paisa (O199).
            last = booked_left(corrected, corrects, on)
            if booked[pk] > last:
                raise ValidationError({corrects: [
                    f"{corrected.label()} on {corrected.document()} has {last} left to give back; "
                    f"this note gives back {booked[pk]}."]})
            continue
        cap = left if by_value_only or going[pk] == quantity else (
            left * going[pk] / quantity if quantity > 0 else Decimal("0"))
        if worth[pk] > cap:
            share = "" if cap == left else f" ({plain(going[pk])} of {plain(quantity)} left: {round_money(max(cap, Decimal('0')))})"
            raise ValidationError({corrects: [
                f"{corrected.label()} on {corrected.document()} has {round_money(max(left, Decimal('0')))} "
                f"left to give back{share}; this note gives back {round_money(worth[pk])}."]})


def given_back_or_waiting(corrected, corrects, but=None, **only):
    """
    How much of `corrected`, an invoice or bill line, the notes on it give
    back: posted ones and drafts still waiting alike, less line `but` (the
    one being saved). `only` narrows the notes (a claim gives back money,
    not goods). The room a new note is offered (quantity_creditable,
    quantity_debitable) and a return's allocation both ask it (O336, O335):
    counted only once posted, a store hand's return waited as a draft of
    10 while AR credited the same 10 by hand, and a second return's draft
    took the bill the first had already taken. Posting asks posted notes
    alone (refuse_correcting_past_what_it_holds): a draft waiting does not
    stop the draft before it posting.
    """
    notes = type(corrected)._base_manager.filter(**{corrects: corrected}, **only)
    if but is not None and but.pk:
        notes = notes.exclude(pk=but.pk)
    return notes.aggregate(total=models.Sum("quantity"))["total"] or Decimal("0")


def refuse_holding_past_what_is_left(line, corrects, **only):
    """
    At save: a note's line gives back no more of the line it names than
    is left of it, posted notes and drafts waiting counted, this note's
    other lines on it too (given_back_or_waiting). Every way a note line
    is made comes here; posting asks again, of posted notes alone.
    """
    if not getattr(line, f"{corrects}_id"):
        return
    from apps.core.api import plain

    corrected = getattr(line, corrects)
    # Its quantity as stored, not as the object in hand was read.
    whole = type(corrected)._base_manager.values_list("quantity", flat=True).get(pk=corrected.pk)
    left = whole - given_back_or_waiting(corrected, corrects, but=line, **only)
    if line.quantity > left:
        raise ValidationError({corrects: [
            f"Only {plain(max(left, Decimal('0')))} of {corrected.label()} on {corrected.document()} is left to "
            f"give back, notes still waiting as drafts counted; this line gives back {plain(line.quantity)}."]})


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


def refuse_switched_off_settings(document):
    """
    A fiscal position or charge type switched off is used by no new document (O283); each flag was a
    filter on a screen and nothing more. Asked as the document posts, of what it names then.
    """
    profile = getattr(document.tax_party(), "tax_profile", None)
    position = getattr(profile, "fiscal_position", None) if profile is not None else None
    if position is not None and not position.is_active:
        raise ValidationError(
            f"{document.tax_party()}'s fiscal position {position} is switched off, so there is no saying which "
            "taxes replace which. Give the party a position in use, or switch it on again.")
    for line in document.lines.all():
        charge = getattr(line, "charge", None) if line.is_charge() else None
        if charge is not None and not charge.is_active:
            raise ValidationError(f"Charge {charge.code} is switched off; a new document does not bill it.")


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

        # In key order: each line's write below holds its row, and the lines come back in whatever order
        # the database keeps them; one edited earlier in the transaction came last (sales.invoiceline 1
        # locked after 13), against the lock order.
        if original is None:
            refuse_switched_off_settings(self)
        for line, amounts in sorted(self.line_tax_amounts().items(), key=lambda pair: pair[0].pk):
            for tax, amount in amounts:
                if original is None and not tax.is_active:
                    # A note bears what its original bore; anything else is new (O283).
                    raise ValidationError(
                        f"Tax {tax.code} is switched off; a new document is not taxed at it. Take it off "
                        f"{line.label()}, or switch it on again.")
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
