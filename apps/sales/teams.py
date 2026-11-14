"""
Sales teams and what they are set to sell.

A team is reps under a leader; a target is net revenue a team or a rep
is to bill in a span, usually a month or a quarter. The report puts
each target against what was posted in its own span: invoices less
credit notes, in rupees at the rate each invoice posted at, by the rep
each invoice carries (an invoice carries the
customer's rep when it is made, so moving a customer to another rep
does not move past sales). A team's actual is its members' as they
stand today; a rep moved between teams takes their history along.
"""

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

from apps.core.models import AuditModel, Party, to_date

from .models import InvoiceLine, SalesRep, sales_between

ZERO = Decimal("0")
ONE = Decimal("1")
PAISA = Decimal("0.01")
TENTH = Decimal("0.1")


class SalesTeam(AuditModel):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=128)
    leader = models.ForeignKey(SalesRep, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                               help_text="The rep who answers for the team's number.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.name


class SalesTarget(AuditModel):
    """Net revenue a team or a rep is to bill between two dates, one or the other."""

    team = models.ForeignKey(SalesTeam, null=True, blank=True, on_delete=models.PROTECT, related_name="targets")
    rep = models.ForeignKey(SalesRep, null=True, blank=True, on_delete=models.PROTECT, related_name="targets")
    period_start = models.DateField()
    period_end = models.DateField()
    amount = models.DecimalField(max_digits=18, decimal_places=2, help_text="Net of tax, before credit notes.")
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-period_start", "id"]
        constraints = [
            models.CheckConstraint(check=Q(amount__gte=0), name="sales_target_amount_not_negative"),
            models.CheckConstraint(check=Q(period_end__gte=models.F("period_start")), name="sales_target_period_in_order"),
            models.CheckConstraint(check=(Q(team__isnull=False, rep__isnull=True) | Q(team__isnull=True, rep__isnull=False)),
                                   name="sales_target_for_a_team_or_a_rep"),
            models.UniqueConstraint(fields=["team", "period_start"], condition=Q(team__isnull=False),
                                    name="one_team_target_per_period_start"),
            models.UniqueConstraint(fields=["rep", "period_start"], condition=Q(rep__isnull=False),
                                    name="one_rep_target_per_period_start"),
        ]

    def __str__(self):
        return f"{self.who()} {self.period_start:%d %b %Y} to {self.period_end:%d %b %Y}: {self.amount}"

    def who(self):
        return str(self.team) if self.team_id else (str(self.rep.party) if self.rep_id else "nobody")

    def save(self, *args, **kwargs):
        self.period_start, self.period_end = to_date(self.period_start), to_date(self.period_end)
        if bool(self.team_id) == bool(self.rep_id):
            raise ValidationError({"team": ["A target is a team's or one rep's, not both and not neither."]})
        if self.period_end < self.period_start:
            raise ValidationError({"period_end": ["The target ends before it starts."]})
        if self.amount is not None and self.amount < 0:
            raise ValidationError({"amount": ["A target is nothing or more."]})
        super().save(*args, **kwargs)


def net_by_rep(date_from=None, date_to=None):
    """
    {rep party id: net revenue in the base currency} between two dates,
    invoices less credit notes, from what each line recorded when it
    posted, at the rate its invoice posted at: a target is in rupees, and
    a dollar invoice's dollars are not. Unrounded, so a team adds its
    members before anything is rounded; an invoice nobody carries counts
    under None.
    """
    invoices = sales_between(date_from, date_to)
    totals = defaultdict(lambda: ZERO)
    recorded = InvoiceLine.objects.filter(invoice__in=invoices, invoice__taxes_recorded=True, posted_net__isnull=False)
    for sign, lines in ((Decimal("1"), recorded.filter(invoice__credits__isnull=True)),
                        (Decimal("-1"), recorded.filter(invoice__credits__isnull=False))):
        for row in lines.order_by().values("invoice__sales_rep", "invoice__exchange_rate").annotate(
                net=models.Sum("posted_net")):
            totals[row["invoice__sales_rep"]] += sign * row["net"] * (row["invoice__exchange_rate"] or ONE)
    # Lines posted before their net was recorded (record_posted_totals
    # fills them at the next start): worked out, not dropped.
    unrecorded = invoices.filter(Q(taxes_recorded=False) | Q(lines__posted_net__isnull=True)).distinct()
    for invoice in unrecorded.prefetch_related("lines"):
        sign = Decimal("-1") if invoice.is_credit_note() else Decimal("1")
        for line in invoice.lines.all():
            if invoice.taxes_recorded and line.posted_net is not None:
                continue
            totals[invoice.sales_rep_id] += sign * line.net_amount() * (invoice.exchange_rate or ONE)
    return dict(totals)


def targets_report(date_from=None, date_to=None):
    """
    Every target whose span touches the window, against what its team or
    rep posted in the target's own span: [{id, kind, who, period_start,
    period_end, target, actual, percent, shortfall}].
    """
    date_from, date_to = to_date(date_from), to_date(date_to)
    # Teams before reps on either database: SQLite sorts a null code
    # first, PostgreSQL last, unless told.
    targets = SalesTarget.objects.select_related("team", "rep__party").order_by(
        "period_start", F("team__code").asc(nulls_last=True), "rep__party__name")
    if date_from:
        targets = targets.filter(period_end__gte=date_from)
    if date_to:
        targets = targets.filter(period_start__lte=date_to)
    members = defaultdict(list)
    for rep in SalesRep.objects.filter(team__isnull=False).select_related("party"):
        members[rep.team_id].append(rep.party_id)
    nets = {}
    rows = []
    for target in targets:
        span = (target.period_start, target.period_end)
        if span not in nets:
            nets[span] = net_by_rep(*span)
        parties = members[target.team_id] if target.team_id else [target.rep.party_id]
        # At the paisa on either database: SQLite sums a 2-place column to
        # whatever places the figures had, PostgreSQL to the column's.
        actual = sum((nets[span].get(party, ZERO) for party in parties), ZERO).quantize(PAISA)
        percent = ((actual / target.amount) * 100).quantize(TENTH, ROUND_HALF_UP) if target.amount else None
        rows.append({
            "id": target.pk, "kind": "team" if target.team_id else "rep", "who": target.who(),
            "team": target.team_id, "rep": target.rep_id,
            "period_start": target.period_start, "period_end": target.period_end,
            "target": target.amount, "actual": actual, "percent": percent,
            "shortfall": max(target.amount - actual, ZERO).quantize(PAISA),
        })
    return rows


def rep_label(party_id, parties=None):
    """How the sales report names a rep: the party, or a dash for an invoice nobody carries."""
    if party_id is None:
        return "—"
    party = (parties or {}).get(party_id) or Party.objects.filter(pk=party_id).first()
    return str(party) if party else "—"
