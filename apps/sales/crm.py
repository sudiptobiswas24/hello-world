"""
Before the order: the enquiry that may become a customer, what it may
be worth, and what was said and done on the way. Quote-to-cash begins
at the quotation; this is what comes before it, kept by the rep.

A lead is a company that asked, not yet a party. Converted, it becomes
a customer the rep carries and an opportunity to win; lost, it says why.
An opportunity is one piece of business with a stage, a value and a
chance, quoted from here, ending won (an order) or lost (a reason). An
activity is a call, a visit or a note against one of them, with a
follow-up date the morning checks watch. A campaign is what brought the
leads, and what it brought back.

A rep sees their own, and leads nobody owns, so they can take them.
Converted and lost are closed: what they said then stands.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from apps.core.models import AuditModel, DocumentSequence, Party, PartyRole, PartyRoleAssignment, serialised, to_date

ZERO = Decimal("0")
PAISA = Decimal("0.01")
NOT_A_REP = "A rep's leads, opportunities and activities are their own: the owner is you."


class LeadSource(models.TextChoices):
    REFERRAL = "referral", "Referral"
    WALK_IN = "walk_in", "Walked in"
    PHONE = "phone", "Phone or WhatsApp"
    WEB = "web", "Website"
    EXHIBITION = "exhibition", "Exhibition"
    CAMPAIGN = "campaign", "Campaign"
    OTHER = "other", "Other"


class LeadStatus(models.TextChoices):
    NEW = "new", "New"
    WORKING = "working", "Being worked"
    CONVERTED = "converted", "Converted"
    LOST = "lost", "Lost"


class Channel(models.TextChoices):
    EXHIBITION = "exhibition", "Exhibition"
    PRINT = "print", "Print"
    DIGITAL = "digital", "Digital"
    FIELD = "field", "Field visits"
    REFERRAL = "referral", "Referral drive"
    OTHER = "other", "Other"


class Stage(models.TextChoices):
    NEW = "new", "New"
    QUALIFIED = "qualified", "Qualified"
    QUOTED = "quoted", "Quoted"
    WON = "won", "Won"
    LOST = "lost", "Lost"


# The chance a stage carries unless the rep says otherwise.
CHANCE = {Stage.NEW: 10, Stage.QUALIFIED: 30, Stage.QUOTED: 60, Stage.WON: 100, Stage.LOST: 0}
OPEN_STAGES = (Stage.NEW, Stage.QUALIFIED, Stage.QUOTED)


class ActivityKind(models.TextChoices):
    CALL = "call", "Call"
    VISIT = "visit", "Visit"
    EMAIL = "email", "Email or message"
    NOTE = "note", "Note"
    FOLLOW_UP = "follow_up", "Follow-up"


def _active_rep(party):
    from .models import SalesRep

    return SalesRep.objects.filter(party=party, is_active=True).exists()


def _check_owner(owner):
    if owner is not None and not _active_rep(owner):
        raise ValidationError({"owner": f"{owner} is not an active sales rep."})


class Campaign(AuditModel):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=128)
    channel = models.CharField(max_length=16, choices=Channel.choices, default=Channel.OTHER)
    starts_on = models.DateField()
    ends_on = models.DateField(null=True, blank=True)
    budget = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO, help_text="What it was allowed to cost.")
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-starts_on", "code"]
        constraints = [
            models.CheckConstraint(check=Q(budget__gte=0), name="campaign_budget_not_negative"),
            models.CheckConstraint(check=Q(ends_on__isnull=True) | Q(ends_on__gte=F("starts_on")),
                                   name="campaign_ends_after_it_starts"),
        ]

    def __str__(self):
        return f"{self.code} {self.name}"

    def save(self, *args, **kwargs):
        if self.ends_on is not None and self.ends_on < self.starts_on:
            raise ValidationError({"ends_on": "A campaign ends after it starts."})
        if self.budget < 0:
            raise ValidationError({"budget": "A budget is nothing or more."})
        super().save(*args, **kwargs)

    def results(self):
        """What it brought: leads, how many became customers, the business opened and the business won."""
        leads = self.leads.aggregate(
            leads=Count("id"), converted=Count("id", filter=Q(status=LeadStatus.CONVERTED)))
        business = self.opportunities.aggregate(
            opportunities=Count("id"), won=Count("id", filter=Q(stage=Stage.WON)),
            open_value=Sum("value", filter=Q(stage__in=OPEN_STAGES)), won_value=Sum("value", filter=Q(stage=Stage.WON)))
        return {
            "leads": leads["leads"],
            "converted": leads["converted"],
            "opportunities": business["opportunities"],
            "open_value": (business["open_value"] or ZERO).quantize(PAISA),
            "won": business["won"],
            "won_value": (business["won_value"] or ZERO).quantize(PAISA),
            "budget": self.budget,
        }


class Lead(AuditModel):
    number = models.CharField(max_length=32, blank=True, editable=False)
    company_name = models.CharField(max_length=255)
    contact_name = models.CharField(max_length=128, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    email = models.CharField(max_length=254, blank=True)
    city = models.CharField(max_length=64, blank=True)
    state = models.CharField(max_length=64, blank=True)
    source = models.CharField(max_length=16, choices=LeadSource.choices, default=LeadSource.OTHER)
    campaign = models.ForeignKey(Campaign, null=True, blank=True, on_delete=models.PROTECT, related_name="leads")
    interest = models.CharField(max_length=255, blank=True,
                                help_text="What they asked for: 50 kg cement sacks, twenty thousand a month.")
    owner = models.ForeignKey(Party, null=True, blank=True, on_delete=models.PROTECT, related_name="leads_owned",
                              help_text="The rep who carries it. Empty, any rep may take it.")
    status = models.CharField(max_length=16, choices=LeadStatus.choices, default=LeadStatus.NEW)
    converted_party = models.ForeignKey(Party, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                        editable=False)
    converted_on = models.DateField(null=True, blank=True, editable=False)
    lost_reason = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["number"], condition=~Q(number=""), name="unique_lead_number"),
        ]

    def __str__(self):
        return f"{self.number} {self.company_name}"

    def is_open(self):
        return self.status in (LeadStatus.NEW, LeadStatus.WORKING)

    def save(self, *args, **kwargs):
        if not self.company_name.strip():
            raise ValidationError({"company_name": "Who asked?"})
        _check_owner(self.owner)
        closing = getattr(self, "_closing", False)
        if self._state.adding:
            self.number = DocumentSequence.next_for("sales.lead", timezone.localdate(), name="Leads", prefix="LD-")
        elif not closing:
            before = Lead.objects.get(pk=self.pk)
            if not before.is_open():
                raise ValidationError(f"{self} is {before.get_status_display().lower()}; what it said then stands.")
        if not closing and not self.is_open():
            raise ValidationError({"status": "A lead is converted with convert() and lost with lose()."})
        super().save(*args, **kwargs)

    def _close(self, status, fields):
        self.status = status
        self._closing = True
        try:
            self.save(update_fields=[*fields, "status", "updated_at"])
        finally:
            self._closing = False

    def delete(self, *args, **kwargs):
        if not self.is_open():
            raise ValidationError(f"{self} is {self.get_status_display().lower()}; it stays.")
        return super().delete(*args, **kwargs)

    @serialised("status")
    def convert(self, user, code, name=None, on_date=None):
        """Make the customer and the opportunity; the party is the rep's, as one made by hand would be."""
        from apps.core.scoping import created, refuse_create

        if not self.is_open():
            raise ValidationError(f"{self} is {self.get_status_display().lower()}.")
        code = (code or "").strip()
        if not code:
            raise ValidationError({"code": "Give the new customer its code."})
        said = refuse_create(user, PartyRole.CUSTOMER)
        if said:
            raise ValidationError(said)
        if Party.objects.filter(code=code).exists():
            raise ValidationError({"code": f"{code!r} is already a party; give the new customer another code, "
                                           "or make an opportunity on the party there is."})
        party = Party.objects.create(code=code, name=(name or self.company_name).strip(), email=self.email,
                                     phone=self.phone, created_by=user, updated_by=user)
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.CUSTOMER, created_by=user, updated_by=user)
        created(party, PartyRole.CUSTOMER, user)
        # A lead nobody owned is the converting rep's, as the customer just made is: with no
        # owner the opportunity was nobody's, so the rep who made it read 404.
        opportunity = Opportunity.objects.create(
            customer=party, title=self.interest or f"First business with {party.name}", lead=self,
            campaign=self.campaign, owner=self.owner or owner_for(user, None), created_by=user, updated_by=user)
        self.converted_party = party
        self.converted_on = to_date(on_date) or timezone.localdate()
        self._close(LeadStatus.CONVERTED, ["converted_party", "converted_on"])
        return party, opportunity

    # How warm a lead is, read from its facts each time and never stored:
    # where it came from, whether it can be reached, whether it said what
    # it wants, what has been done about it, and how long it has sat.
    SOURCE_POINTS = {"referral": 25, "exhibition": 20, "campaign": 15, "web": 10, "walk_in": 10, "phone": 10, "other": 0}

    def score_reasons(self, today=None):
        """[(points, why)], the points adding to the score before it is capped at 0 to 100; nothing once closed."""
        if not self.is_open():
            return []
        today = today or timezone.localdate()
        reasons = [(self.SOURCE_POINTS.get(self.source, 0), f"from {self.get_source_display().lower()}")]
        if self.phone:
            reasons.append((10, "a phone number"))
        if self.email:
            reasons.append((10, "an email address"))
        if self.contact_name:
            reasons.append((5, "a named contact"))
        if self.interest:
            reasons.append((15, "said what they want"))
        if self.campaign_id:
            reasons.append((5, "from a campaign"))
        if self.owner_id:
            reasons.append((5, "a rep on it"))
        # A list annotates `done_count` once for the page; one lead asks.
        done = getattr(self, "done_count", None)
        if done is None:
            done = self.activities.filter(done_on__isnull=False).count() if self.pk else 0
        if done:
            reasons.append((min(done, 4) * 5, f"{done} call{'s' if done != 1 else ''} or visit{'s' if done != 1 else ''} made"))
        age = (today - to_date(self.created_at)).days if self.created_at else 0
        if age <= 14:
            reasons.append((10, "fresh: asked within a fortnight"))
        elif age > 90:
            reasons.append((-15, f"stale: {age} days without becoming a customer"))
        return reasons

    def score(self, today=None):
        return max(0, min(100, sum(points for points, _ in self.score_reasons(today))))

    def score_summary(self, today=None):
        if not self.is_open():
            return f"not scored: {self.get_status_display().lower()}"
        return ", ".join(f"{why} {points:+d}" for points, why in self.score_reasons(today))

    def email_them(self, subject, body, user=None):
        """
        A mail to the lead's address, written in its history and as an
        email activity done today. The activity is written first, so a
        refusal (the lead's rep no longer active) sends nothing, and a
        mail that fails to go takes the activity with it.
        """
        from apps.core.mail import deliver

        if not self.email:
            raise ValidationError({"email": [f"{self} has no email address."]})
        subject = " ".join(subject.split())
        if not subject:
            raise ValidationError({"subject": ["A mail has a subject."]})
        with transaction.atomic():
            Activity.objects.create(kind=ActivityKind.EMAIL, lead=self, summary=subject[:255], notes=body or "",
                                    owner=owner_for(user, self.owner) if user is not None else self.owner,
                                    done_on=timezone.localdate())
            return deliver(self, self.email, subject, body, user=user, what=f"Mail '{subject}'")

    @serialised("status")
    def lose(self, reason):
        if not self.is_open():
            raise ValidationError(f"{self} is {self.get_status_display().lower()}.")
        if not (reason or "").strip():
            raise ValidationError({"reason": "Say why it was lost; the next rep reads it."})
        self.lost_reason = reason.strip()
        self._close(LeadStatus.LOST, ["lost_reason"])


class Opportunity(AuditModel):
    number = models.CharField(max_length=32, blank=True, editable=False)
    customer = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="opportunities")
    title = models.CharField(max_length=128, help_text="The business, in a line: 20,000 cement sacks a month.")
    lead = models.ForeignKey(Lead, null=True, blank=True, on_delete=models.PROTECT, related_name="opportunities",
                             editable=False)
    campaign = models.ForeignKey(Campaign, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="opportunities")
    owner = models.ForeignKey(Party, null=True, blank=True, on_delete=models.PROTECT,
                              related_name="opportunities_owned", help_text="The rep who carries it.")
    stage = models.CharField(max_length=16, choices=Stage.choices, default=Stage.NEW)
    value = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO,
                                help_text="What the order would be worth, before tax.")
    expected_on = models.DateField(null=True, blank=True, help_text="When the order is expected.")
    probability = models.PositiveSmallIntegerField(null=True, blank=True,
                                                   help_text="Chance in percent; empty takes the stage's own.")
    quotation = models.ForeignKey("sales.Quotation", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="opportunities", editable=False)
    sales_order = models.ForeignKey("sales.SalesOrder", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="opportunities", editable=False)
    lost_reason = models.CharField(max_length=255, blank=True, editable=False)
    closed_on = models.DateField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "opportunities"
        constraints = [
            models.UniqueConstraint(fields=["number"], condition=~Q(number=""), name="unique_opportunity_number"),
            models.CheckConstraint(check=Q(value__gte=0), name="opportunity_value_not_negative"),
            models.CheckConstraint(check=Q(probability__isnull=True) | Q(probability__lte=100),
                                   name="opportunity_chance_is_a_percent"),
        ]

    def __str__(self):
        return f"{self.number} {self.title}"

    def is_open(self):
        return self.stage in OPEN_STAGES

    def chance(self):
        return self.probability if self.probability is not None else CHANCE[Stage(self.stage)]

    def weighted_value(self):
        return (self.value * self.chance() / 100).quantize(PAISA)

    def save(self, *args, **kwargs):
        if not self.title.strip():
            raise ValidationError({"title": "Say what the business is."})
        if self.value < 0:
            raise ValidationError({"value": "An opportunity is worth nothing or more."})
        if self.probability is not None and self.probability > 100:
            raise ValidationError({"probability": "A chance is a percent, up to 100."})
        _check_owner(self.owner)
        if not PartyRoleAssignment.objects.filter(party_id=self.customer_id, role=PartyRole.CUSTOMER).exists():
            raise ValidationError({"customer": f"{self.customer} is not a customer."})
        closing = getattr(self, "_closing", False)
        if self._state.adding:
            self.number = DocumentSequence.next_for("sales.opportunity", timezone.localdate(), name="Opportunities",
                                                    prefix="OP-")
        elif not closing:
            before = Opportunity.objects.get(pk=self.pk)
            if not before.is_open():
                raise ValidationError(f"{self} is {before.get_stage_display().lower()}; what closed it stands.")
        if not closing and self.stage not in OPEN_STAGES:
            raise ValidationError({"stage": "An opportunity is won with win() and lost with lose(), each saying what closed it."})
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if not self.is_open():
            raise ValidationError(f"{self} is {self.get_stage_display().lower()}; it stays.")
        return super().delete(*args, **kwargs)

    @serialised("stage")
    def quote(self, quotation_date, valid_until=None):
        """A draft quotation for this customer, from this rep; the lines are the rep's to add."""
        from .models import Quotation

        if not self.is_open():
            raise ValidationError(f"{self} is {self.get_stage_display().lower()}.")
        if self.quotation_id:
            raise ValidationError(f"{self} is quoted already, as {self.quotation.number}.")
        quotation_date = to_date(quotation_date)
        quotation = Quotation.objects.create(customer=self.customer, quotation_date=quotation_date,
                                             valid_until=to_date(valid_until) if valid_until else None,
                                             sales_rep=self.owner, reference=self.number)
        self.quotation = quotation
        self.stage = Stage.QUOTED
        self.save(update_fields=["quotation", "stage", "updated_at"])
        return quotation

    def _close(self, stage, on_date, fields):
        self.stage = stage
        self.closed_on = to_date(on_date) or timezone.localdate()
        self._closing = True
        try:
            self.save(update_fields=["stage", "closed_on", *fields, "updated_at"])
        finally:
            self._closing = False

    @serialised("stage")
    def win(self, sales_order=None, on_date=None):
        if not self.is_open():
            raise ValidationError(f"{self} is {self.get_stage_display().lower()}.")
        if sales_order is not None and sales_order.customer_id != self.customer_id:
            raise ValidationError({"sales_order": f"{sales_order} is {sales_order.customer}'s, not {self.customer}'s."})
        self.sales_order = sales_order
        self._close(Stage.WON, on_date, ["sales_order"])

    @serialised("stage")
    def lose(self, reason, on_date=None):
        if not self.is_open():
            raise ValidationError(f"{self} is {self.get_stage_display().lower()}.")
        if not (reason or "").strip():
            raise ValidationError({"reason": "Say why it was lost."})
        self.lost_reason = reason.strip()
        self._close(Stage.LOST, on_date, ["lost_reason"])


class Activity(AuditModel):
    kind = models.CharField(max_length=16, choices=ActivityKind.choices, default=ActivityKind.CALL)
    lead = models.ForeignKey(Lead, null=True, blank=True, on_delete=models.CASCADE, related_name="activities")
    opportunity = models.ForeignKey(Opportunity, null=True, blank=True, on_delete=models.CASCADE,
                                    related_name="activities")
    party = models.ForeignKey(Party, null=True, blank=True, on_delete=models.CASCADE, related_name="activities",
                              help_text="A customer, where it is about neither a lead nor an opportunity.")
    owner = models.ForeignKey(Party, null=True, blank=True, on_delete=models.PROTECT, related_name="activities_owned",
                              help_text="The rep who did it, or will.")
    summary = models.CharField(max_length=255)
    notes = models.TextField(blank=True)
    due_on = models.DateField(null=True, blank=True, help_text="For a follow-up: the day it is due; the morning checks watch it.")
    done_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "activities"
        constraints = [
            models.CheckConstraint(
                check=(Q(lead__isnull=False, opportunity__isnull=True, party__isnull=True)
                       | Q(lead__isnull=True, opportunity__isnull=False, party__isnull=True)
                       | Q(lead__isnull=True, opportunity__isnull=True, party__isnull=False)),
                name="activity_about_one_thing"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.summary}"

    def subject(self):
        return self.lead or self.opportunity or self.party

    def save(self, *args, **kwargs):
        if sum(1 for value in (self.lead_id, self.opportunity_id, self.party_id) if value) != 1:
            raise ValidationError("An activity is about one lead, one opportunity or one customer.")
        if not self.summary.strip():
            raise ValidationError({"summary": "Say what was done, or is to be."})
        _check_owner(self.owner)
        super().save(*args, **kwargs)

    @serialised("done_on")
    def done(self, on_date=None):
        if self.done_on is not None:
            raise ValidationError(f"{self} was done on {self.done_on}.")
        self.done_on = to_date(on_date) or timezone.localdate()
        self.save(update_fields=["done_on", "updated_at"])


# -- who sees what -------------------------------------------------------


def for_rep(queryset, user, unowned_too=False):
    """Rows a login may see: everything, or their own (and the unowned, where asked)."""
    from .scoping import NOBODY, UNLIMITED, rep_limit

    rep = rep_limit(user)
    if rep is UNLIMITED:
        return queryset
    if rep is NOBODY:
        return queryset.none()
    mine = Q(owner=rep)
    if unowned_too:
        mine |= Q(owner__isnull=True)
    return queryset.filter(mine)


def owner_for(user, owner):
    """The owner a login may set: a limited rep sets only themselves, and gets themselves when they say nothing."""
    from .scoping import NOBODY, NOT_LINKED, UNLIMITED, rep_limit

    rep = rep_limit(user)
    if rep is UNLIMITED:
        return owner
    if rep is NOBODY:
        raise ValidationError({"owner": NOT_LINKED})
    if owner is not None and owner.pk != rep.pk:
        raise ValidationError({"owner": NOT_A_REP})
    return rep


def pipeline(user):
    """Each stage's count, value and weighted value, over what the login may see."""
    by_stage = {stage: [] for stage in Stage}
    for row in for_rep(Opportunity.objects.all(), user):
        by_stage[Stage(row.stage)].append(row)
    return [{"stage": stage.value, "label": stage.label, "count": len(found),
             "value": sum((row.value for row in found), ZERO).quantize(PAISA),
             "weighted": sum((row.weighted_value() for row in found), ZERO).quantize(PAISA)}
            for stage, found in by_stage.items()]


def follow_ups_due(user, day=None):
    """Activities not done whose day has come, among the login's own."""
    day = day or timezone.localdate()
    return for_rep(Activity.objects.filter(done_on__isnull=True, due_on__lte=day), user).count()
