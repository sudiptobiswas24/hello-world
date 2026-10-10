"""
What was measured, what it should have been, and what was decided.

A woven sack is sold on numbers: eighty-seven grammes a square metre,
a thousand denier, a tensile strength the customer's filling line
needs. The specification already holds those numbers and their
tolerances — so an inspection plan is not something anybody should be
typing either, and for a specified product it is generated the same way
its bill of materials is.

This app depends on inventory and core and on nothing else, and it
matters that it stays that way. An inspection attaches to a LOT,
because a lot is the one subject every module already shares: a run
produced it, a receipt brought it in, a delivery ships it. Attaching
instead to a production entry would make quality depend on
manufacturing, and manufacturing already has to depend on quality to
refuse issuing a failed batch. One of those two has to give, and it is
this one.

Three things here are decisions rather than mechanics:

**A reading is not a result.** Three readings of a fabric roll's GSM
are three facts; whether the roll passes depends on whether the plan
says every reading must be inside the limits or the average must be.
For a weight both are used in practice and they disagree, so the plan
says which.

**A failure is not a disposition.** A roll at 84 GSM against 87.5 has
failed, and the customer may still take it at a discount. That is a
concession — a decision, by a named person, recorded as one. Silently
passing it would leave nothing to answer with when the next one is
argued about.

**An uninspected batch of something that must be inspected is not
released.** A plan marked mandatory means what it says: until somebody
has measured the roll and passed it, it does not leave the building and
it does not go into another run.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.core import windows
from apps.core.approvals import authors, check_not_raised_by
from apps.core.models import AuditModel, DocumentSequence, serialised, to_date
from apps.inventory.models import Item, Lot


class QualitySettings(AuditModel):
    """
    The one thing about quality that is a policy rather than a rule.
    """

    concessions_need_a_second_person = models.BooleanField(
        default=False,
        help_text="Whether the person who measured a batch may also be the "
                  "one who takes it out of specification. Off by default, and "
                  "deliberately: in a small plant the quality inspector and "
                  "the manager are genuinely the same person, and refusing "
                  "would only teach them to type a second name that is not "
                  "true. On, it is the segregation an auditor asks about. "
                  "Either way `self_approved()` says which happened, and the "
                  "admin shows it.",
    )

    class Meta:
        verbose_name_plural = "quality settings"

    def __str__(self):
        return "Quality settings"

    def save(self, *args, **kwargs):
        if self._state.adding and QualitySettings.objects.exists():
            raise ValidationError("There is one set of quality settings; change it rather than adding another.")
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        return cls.objects.first() or cls.objects.create()


class CharacteristicKind(models.TextChoices):
    MEASURED = "measured", "Measured against limits"
    ATTRIBUTE = "attribute", "Present or absent"


class Evaluation(models.TextChoices):
    EVERY = "every", "Every reading must pass"
    MEAN = "mean", "The average must pass"


class Characteristic(AuditModel):
    """
    Something that can be measured about goods.

    Shared across plans, because "GSM" is one thing however many
    products are checked for it, and a recall that has to ask "which
    batches did we ever measure below target" wants one row to ask
    about rather than forty.
    """

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    kind = models.CharField(
        max_length=16, choices=CharacteristicKind.choices,
        default=CharacteristicKind.MEASURED,
    )
    uom = models.ForeignKey(
        "core.UnitOfMeasure", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
        help_text="What the reading is in. Required for anything measured — a "
                  "number with no unit is the mistake this codebase keeps "
                  "finding; left blank for something that is simply present or "
                  "absent.",
    )
    decimal_places = models.PositiveSmallIntegerField(
        default=2,
        help_text="How precisely the instrument reads. A balance to two "
                  "places and a micrometer to four are different instruments "
                  "and a report that shows both to the same precision is "
                  "claiming something about one of them.",
    )
    is_active = models.BooleanField(default=True)
    needs_calibrated_instrument = models.BooleanField(
        default=False,
        help_text="Every reading names the instrument it was taken on, and that "
                  "instrument is in calibration on the day.",
    )

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def is_measured(self):
        return self.kind == CharacteristicKind.MEASURED

    def save(self, *args, **kwargs):
        if self.is_measured() and self.uom_id is None:
            raise ValidationError(
                f"{self.code} is measured and does not say in what. A reading "
                "of 87.5 against a limit of 91 means nothing until both say "
                "grammes a square metre."
            )
        if not self.is_measured() and self.uom_id is not None:
            raise ValidationError(
                f"{self.code} is present or absent and cannot be counted in "
                f"{self.uom}."
            )
        super().save(*args, **kwargs)


class InspectionPlan(AuditModel):
    """
    What must be checked about one item, and how strictly.

    One per item at a time: a second plan for the same thing on the same
    day would be two answers to "has this passed", and the release check
    would take whichever the database happened to return.

    "At a time" is a window, because a specification staged for the
    first of next month brings its own limits with it, and the plan it
    builds has to wait its turn beside the one it replaces rather than
    colliding with it.
    """

    item = models.ForeignKey(
        Item, on_delete=models.PROTECT, related_name="inspection_plans"
    )
    name = models.CharField(max_length=255, blank=True)
    is_mandatory = models.BooleanField(
        default=True,
        help_text="A batch of this item is not released until somebody has "
                  "measured it and passed it. Off for an item where the plan "
                  "is advisory — a record of what was checked rather than a "
                  "gate.",
    )
    is_computed = models.BooleanField(
        default=False, editable=False,
        help_text="Set when a product specification works this plan out. Such "
                  "a plan refuses to be edited by hand: the edit would survive "
                  "until the next rebuild and no longer.",
    )
    valid_from = models.DateField(
        null=True, blank=True,
        help_text="The first day these limits apply. Blank is from the "
                  "beginning.",
    )
    valid_to = models.DateField(
        null=True, blank=True,
        help_text="The last day these limits apply. Blank is open-ended.",
    )
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["item__sku"]
        constraints = [
            # Overlapping windows are refused on save. These keep the
            # two commonest overlaps impossible in the table itself,
            # where a bulk write cannot walk past them.
            models.UniqueConstraint(
                fields=["item"],
                condition=Q(is_active=True, valid_to__isnull=True),
                name="one_open_ended_plan_per_item",
            ),
            models.UniqueConstraint(
                fields=["item"],
                condition=Q(is_active=True, valid_from__isnull=True),
                name="one_unstarted_plan_per_item",
            ),
            models.CheckConstraint(
                check=Q(valid_from__isnull=True) | Q(valid_to__isnull=True)
                | Q(valid_to__gte=F("valid_from")),
                name="plan_window_runs_forwards",
            ),
        ]

    def __str__(self):
        return self.name or f"Inspection plan for {self.item.sku}"

    def computed_by(self):
        """Whatever works this plan out, found by reflection rather than name."""
        for relation in self._meta.related_objects:
            if not relation.one_to_one:
                continue
            owner = getattr(self, relation.get_accessor_name(), None)
            if owner is not None:
                return owner
        return None

    def _check_window(self):
        """No two active plans for one item on the same day."""
        if not self.is_active:
            return
        if windows.runs_backwards(self.valid_from, self.valid_to):
            raise ValidationError(
                f"{self} would run from {self.valid_from} to {self.valid_to}, "
                "which is backwards."
            )
        others = InspectionPlan.objects.filter(
            item_id=self.item_id, is_active=True
        )
        if self.pk:
            others = others.exclude(pk=self.pk)
        for other in others:
            if windows.overlaps(
                self.valid_from, self.valid_to, other.valid_from, other.valid_to
            ):
                raise ValidationError(
                    f"{other} already sets the limits for {self.item} over "
                    f"part of {windows.label(self.valid_from, self.valid_to)}. "
                    "Close its window first — on the day before this one "
                    "starts."
                )

    def save(self, *args, **kwargs):
        if self.is_computed and not getattr(self, "_rebuilding", False):
            raise ValidationError(
                f"{self} is computed from "
                f"{self.computed_by() or 'a specification'}. Change that; this "
                "plan is rebuilt from it."
            )
        self._check_window()
        if self.is_mandatory and self.item.tracking == "none":
            raise ValidationError(
                f"{self.item} is not tracked by batch, so there is nothing for "
                "an inspection to be about and nothing for the release check "
                "to ask. Track it by lot, or make the plan advisory."
            )
        super().save(*args, **kwargs)


class PlanLine(AuditModel):
    """One characteristic, its limits, and how many to look at."""

    plan = models.ForeignKey(
        InspectionPlan, on_delete=models.CASCADE, related_name="lines"
    )
    characteristic = models.ForeignKey(
        Characteristic, on_delete=models.PROTECT, related_name="plan_lines"
    )
    target = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True,
        help_text="What it is supposed to be. Recorded even where the limits "
                  "do the gating, because the distance from target is what a "
                  "process is steered by and a pass/fail is not.",
    )
    lower_limit = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True
    )
    upper_limit = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True,
        help_text="One of the two may be blank: a tensile strength has a floor "
                  "and no ceiling, and a contamination count has a ceiling and "
                  "no floor. Both blank would be a line that cannot fail.",
    )
    sample_size = models.PositiveIntegerField(
        default=1,
        help_text="How many to look at. Nobody weighs every sack.",
    )
    aql = models.DecimalField(
        max_digits=6, decimal_places=3, null=True, blank=True,
        help_text="Acceptance quality limit, per cent nonconforming. Given, the sample "
                  "and how many may fail come from the lot size (ISO 2859-1, normal, "
                  "single sampling) and `sample_size` is not used.",
    )
    inspection_level = models.CharField(
        max_length=4, default="II",
        help_text="General I, II, III or special S-1 to S-4. II unless the buyer says.",
    )
    evaluation = models.CharField(
        max_length=8, choices=Evaluation.choices, default=Evaluation.EVERY,
        help_text="Whether every reading must be inside the limits or only "
                  "their average. Both are used in practice and they disagree, "
                  "so the plan says which rather than the reader guessing.",
    )
    derived_from = models.CharField(
        max_length=32, blank=True, editable=False,
        help_text="Which figure on the specification produced this line — "
                  "'gsm', 'denier', 'bag_weight'. Recorded so that whatever "
                  "wants to read the measured GSM back can find it without "
                  "matching on a name somebody may translate.",
    )
    line_number = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["plan", "line_number", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["plan", "characteristic"],
                name="one_plan_line_per_characteristic",
            ),
            models.CheckConstraint(
                check=Q(sample_size__gt=0), name="sample_size_positive"
            ),
        ]

    def __str__(self):
        return f"{self.characteristic.code} on {self.plan}"

    def passes(self, value):
        """Whether one reading is inside the limits."""
        if self.lower_limit is not None and value < self.lower_limit:
            return False
        if self.upper_limit is not None and value > self.upper_limit:
            return False
        return True

    def save(self, *args, **kwargs):
        if self.plan.is_computed and not getattr(self, "_rebuilding", False):
            raise ValidationError(
                f"{self.plan} is computed from "
                f"{self.plan.computed_by() or 'a specification'}, and its lines "
                "with it. Change that."
            )
        if self.aql is not None:
            from .sampling import sampling_plan

            # Asked of a lot of 100 only to have the AQL and level checked.
            sampling_plan(100, self.inspection_level, self.aql)
            if self.evaluation == Evaluation.MEAN:
                raise ValidationError(
                    f"{self.characteristic}: an AQL counts the samples that fail; it is "
                    "not judged on their mean."
                )
        if self._state.adding and not self.characteristic.is_active:
            raise ValidationError(
                f"{self.characteristic} has been retired and cannot be added "
                "to a plan. A limit nobody measures against any more is a line "
                "somebody will fill in."
            )
        if self.characteristic.is_measured():
            if self.lower_limit is None and self.upper_limit is None:
                raise ValidationError(
                    f"{self.characteristic} on {self.plan} has neither a floor "
                    "nor a ceiling, so no reading could ever fail it. Give it "
                    "one, or take the line off."
                )
            if (
                self.lower_limit is not None
                and self.upper_limit is not None
                and self.lower_limit > self.upper_limit
            ):
                raise ValidationError(
                    f"{self.characteristic} on {self.plan} has a floor of "
                    f"{self.lower_limit} above its ceiling of "
                    f"{self.upper_limit}; nothing can be inside it."
                )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.plan.is_computed and not getattr(self, "_rebuilding", False):
            raise ValidationError(
                f"{self.plan} is computed and its lines cannot be taken off "
                "one at a time."
            )
        return super().delete(*args, **kwargs)


class Result(models.TextChoices):
    PASS = "pass", "Within specification"
    FAIL = "fail", "Outside specification"


class Disposition(models.TextChoices):
    ACCEPT = "accept", "Accepted"
    CONCESSION = "concession", "Accepted by concession"
    REWORK = "rework", "Held for rework"
    REJECT = "reject", "Rejected"


class ReleaseStatus(models.TextChoices):
    UNINSPECTED = "uninspected", "Not yet inspected"
    RELEASED = "released", "Released"
    HELD = "held", "Held"


class Inspection(AuditModel):
    """
    One batch, measured, and what was decided about it.

    Posted is immutable here as everywhere else: a wrong inspection is
    voided and another is taken. Editing would leave a released batch
    whose release was decided on readings that no longer exist.
    """

    number = models.CharField(max_length=32, blank=True)
    lot = models.ForeignKey(
        Lot, on_delete=models.PROTECT, related_name="inspections",
        help_text="The batch that was measured. A lot rather than a document, "
                  "because a lot is the subject every module already shares "
                  "and attaching to one of their documents would make this app "
                  "depend on that module.",
    )
    plan = models.ForeignKey(
        InspectionPlan, on_delete=models.PROTECT, related_name="inspections"
    )
    inspected_on = models.DateField()
    inspected_by = models.ForeignKey(
        "core.Party", null=True, blank=True, on_delete=models.PROTECT,
        related_name="inspections_taken",
    )
    disposition = models.CharField(
        max_length=16, choices=Disposition.choices, blank=True,
        help_text="What was decided. Not the same question as whether it "
                  "passed: a roll at 84 against 87.5 has failed, and the "
                  "customer may still take it at a discount.",
    )
    decided_by = models.ForeignKey(
        "core.Party", null=True, blank=True, on_delete=models.PROTECT,
        related_name="quality_decisions",
        help_text="Who took a batch that did not pass. Required for a "
                  "concession: an out-of-specification batch released by "
                  "nobody in particular is what there is nothing to answer "
                  "with when the next one is argued about.",
    )
    decision_note = models.CharField(max_length=255, blank=True)
    result = models.CharField(
        max_length=8, choices=Result.choices, blank=True, editable=False,
        help_text="Worked out from the readings when this posted, and frozen. "
                  "The plan's limits will move; what this batch was judged "
                  "against must not.",
    )
    posted = models.BooleanField(default=False)
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_reason = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    lot_size = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="How many units the sample was drawn from. Needed where a line "
                  "samples by AQL; left blank for a counted item, what is on hand.")
    sampling = models.JSONField(
        default=dict, blank=True, editable=False,
        help_text="The sampling plan each AQL line was judged by, frozen at posting.")

    class Meta:
        ordering = ["-inspected_on", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["number"], condition=~Q(number=""),
                name="inspection_number_unique",
            ),
        ]

    def __str__(self):
        return self.number or f"Draft inspection of {self.lot}"

    def is_voided(self):
        return self.voided_at is not None

    def failed_lines(self):
        """The characteristics that did not pass, as judged at posting."""
        return [
            line for line in self.readings_by_line()
            if line["passed"] is False
        ]

    def readings_by_line(self):
        """
        One row per plan line: its readings, their average, and the
        verdict this inspection recorded for it.
        """
        rows = {}
        for reading in self.readings.select_related(
            "plan_line", "plan_line__characteristic"
        ):
            row = rows.setdefault(reading.plan_line_id, {
                "plan_line": reading.plan_line,
                "characteristic": reading.plan_line.characteristic,
                "values": [],
                "passed": None,
            })
            row["values"].append(reading.value)
            if reading.passed is not None:
                row["passed"] = (
                    reading.passed if row["passed"] is None
                    else (row["passed"] and reading.passed)
                )
        for row in rows.values():
            values = [v for v in row["values"] if v is not None]
            row["mean"] = (
                sum(values, Decimal("0")) / len(values) if values else None
            )
        return list(rows.values())

    def _judge(self):
        """
        Every line's verdict, from its own readings and its own rule.

        Freezes the limits onto each reading as it goes: the plan's
        limits will move — a customer tightens a tolerance, a
        specification is re-cut — and what this batch was judged against
        must not move with them.
        """
        by_line = {}
        for reading in self.readings.select_related(
            "plan_line", "plan_line__characteristic"
        ):
            by_line.setdefault(reading.plan_line_id, []).append(reading)

        overall = Result.PASS
        self.sampling = {}
        for line in self.plan.lines.select_related("characteristic"):
            readings = by_line.get(line.pk, [])
            if line.aql is not None:
                plan = self._sampling_for(line)
                self.sampling[str(line.pk)] = plan
                if len(readings) != plan["sample"]:
                    raise ValidationError(
                        f"{line.characteristic} at AQL {plan['aql']}, level "
                        f"{plan['level']}, on a lot of {self.lot_size} takes "
                        f"{plan['sample']} samples (code {plan['letter']}); this "
                        f"inspection has {len(readings)}."
                    )
            elif len(readings) < line.sample_size:
                raise ValidationError(
                    f"{line.characteristic} asks for {line.sample_size} "
                    f"reading(s) and this inspection has {len(readings)}. A "
                    "sample smaller than the plan's is not the plan's sample."
                )
            verdicts = []
            for reading in readings:
                if line.characteristic.is_measured():
                    if reading.value is None:
                        raise ValidationError(
                            f"A reading of {line.characteristic} has no value."
                        )
                    verdict = line.passes(reading.value)
                else:
                    if reading.present is None:
                        raise ValidationError(
                            f"A reading of {line.characteristic} does not say "
                            "whether it was there."
                        )
                    verdict = reading.present
                reading.lower_limit = line.lower_limit
                reading.upper_limit = line.upper_limit
                reading.passed = verdict
                reading._judging = True
                reading.save(update_fields=[
                    "lower_limit", "upper_limit", "passed", "updated_at"
                ])
                verdicts.append(verdict)
            if line.aql is not None:
                failed = len([verdict for verdict in verdicts if not verdict])
                line_passed = failed <= self.sampling[str(line.pk)]["accept"]
            elif line.evaluation == Evaluation.MEAN and line.characteristic.is_measured():
                values = [r.value for r in readings]
                mean = sum(values, Decimal("0")) / len(values)
                line_passed = line.passes(mean)
            else:
                line_passed = all(verdicts)
            if not line_passed:
                overall = Result.FAIL
        return overall

    def _sampling_for(self, line):
        from apps.core.models import UnitOfMeasureCategory

        from .sampling import sampling_plan

        if self.lot_size is None:
            item = self.lot.item
            on_hand = self.lot.on_hand_at()
            if item.uom.category != UnitOfMeasureCategory.COUNT or \
                    on_hand != on_hand.to_integral_value() or on_hand < 2:
                raise ValidationError(
                    f"{line.characteristic} samples by AQL: say how many units the "
                    "lot holds."
                )
            self.lot_size = int(on_hand)
        return sampling_plan(self.lot_size, line.inspection_level, line.aql)

    def self_approved(self):
        """
        Whether the person who measured it is the person who took it.

        Not refused by default — see `QualitySettings` — but always
        askable, because "who signed this off" is the question a
        concession exists to have an answer to.
        """
        return (
            self.disposition == Disposition.CONCESSION
            and self.decided_by_id is not None
            and self.decided_by_id == self.inspected_by_id
        )

    def raised_by(self):
        """The logins that made or changed it or its readings, and the one it says measured it."""
        found = authors(self, "readings")
        measured = getattr(self.inspected_by, "employee_profile", None) if self.inspected_by_id else None
        if measured is not None and measured.user_id:
            found.add(measured.user_id)
        return found

    @staticmethod
    def _signer(by):
        """The person a login is (hr links them; asked by attribute, so quality imports nothing of hr)."""
        employee = getattr(by, "employee", None)
        if employee is None:
            raise ValidationError(
                "A concession is signed by a person, and your login is not linked to an employee. "
                "HR links it on your record.")
        return employee.party

    @serialised("posted")
    def post(self, by=None):
        """
        Judge it and release or hold the batch. `by` is the login posting
        it: on a concession, the person taking the batch out of
        specification. `decided_by` was a field anyone typed, so an
        Inspector named the manager and passed the second-person rule.
        """
        if self.posted:
            raise ValidationError(f"{self} is already posted.")
        if not self.plan.is_active:
            raise ValidationError(
                f"{self.plan} has been retired. Measuring against limits the "
                "plant has withdrawn would release a batch against a promise "
                "nobody is making any more."
            )
        if to_date(self.inspected_on) > timezone.localdate():
            raise ValidationError(
                f"{self} is dated {self.inspected_on}. Nothing has been "
                "measured yet on a day that has not happened."
            )
        if self.plan.item_id != self.lot.item_id:
            raise ValidationError(
                f"{self.plan} is for {self.plan.item} and {self.lot} is a batch "
                f"of {self.lot.item}."
            )
        if not self.readings.exists():
            raise ValidationError(
                "An inspection with no readings records that somebody walked "
                "past the pallet."
            )
        self.inspected_on = to_date(self.inspected_on)
        self.result = self._judge()
        if not self.disposition:
            self.disposition = (
                Disposition.ACCEPT if self.result == Result.PASS
                else Disposition.REJECT
            )
        if self.disposition == Disposition.ACCEPT and self.result == Result.FAIL:
            raise ValidationError(
                f"{self.lot} did not pass, so it cannot simply be accepted. "
                "Take it by concession, naming who decided and why, or reject "
                "it — the difference is the whole point of recording either."
            )
        if self.disposition == Disposition.CONCESSION:
            if self.result == Result.PASS:
                raise ValidationError(
                    f"{self.lot} passed; there is nothing to concede."
                )
            if by is not None:
                self.decided_by = self._signer(by)
            if self.decided_by_id is None or not self.decision_note:
                raise ValidationError(
                    "A concession needs a name against it and a reason. An "
                    "out-of-specification batch released by nobody in "
                    "particular leaves nothing to answer with when the next "
                    "one is argued about."
                )
            if QualitySettings.get().concessions_need_a_second_person:
                if self.self_approved():
                    raise ValidationError(
                        f"{self.inspected_by} measured this batch and would also "
                        "be taking it out of specification. This plant asks for a "
                        "second person on a concession."
                    )
                check_not_raised_by(by, self.raised_by(), self)
        from .calibration import check_readings

        check_readings(self)
        if not self.number:
            self.number = DocumentSequence.next_for(
                "quality.inspection", self.inspected_on,
                name="Inspections", prefix="QC-",
            )
        self.posted = True
        self.posted_at = timezone.now()
        super().save(update_fields=[
            "number", "inspected_on", "result", "disposition", "decided_by", "posted", "lot_size", "sampling",
            "posted_at", "updated_at",
        ])
        return self

    @serialised("posted", "voided_at")
    def void(self, reason=""):
        """
        Undo a posting.

        The batch's status is whatever its latest standing inspection
        says, so voiding this one hands the question back to the one
        before it — or to nobody, which is 'not yet inspected' and not
        'passed'.
        """
        if not self.posted:
            raise ValidationError(f"{self} is not posted.")
        if self.is_voided():
            raise ValidationError(f"{self} is already voided.")
        if not reason:
            raise ValidationError(
                "Voiding an inspection needs a reason: a batch whose release "
                "was withdrawn and nobody said why is a batch somebody will "
                "release again."
            )
        self.voided_at = timezone.now()
        self.voided_reason = reason
        super().save(update_fields=["voided_at", "voided_reason", "updated_at"])
        return self

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            previous = Inspection.objects.filter(pk=self.pk).first()
            if previous is not None and previous.posted:
                raise ValidationError(
                    f"Cannot modify {self} once it is posted. Void it and take "
                    "another."
                )
        super().save(*args, **kwargs)


class Reading(AuditModel):
    """One measurement, and what it was judged against at the time."""

    inspection = models.ForeignKey(
        Inspection, on_delete=models.CASCADE, related_name="readings"
    )
    plan_line = models.ForeignKey(
        PlanLine, on_delete=models.PROTECT, related_name="readings"
    )
    value = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True,
        help_text="For a measured characteristic. Blank for one that is "
                  "simply present or absent.",
    )
    present = models.BooleanField(
        null=True, blank=True,
        help_text="For a characteristic that is present or absent.",
    )
    sample_reference = models.CharField(
        max_length=64, blank=True,
        help_text="Which roll, which bag off the stack. Kept so a second "
                  "opinion can be taken on the same sample.",
    )
    lower_limit = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True, editable=False
    )
    upper_limit = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True, editable=False,
        help_text="The limits as they stood when this was judged, frozen "
                  "here. A tolerance tightened next year must not retrospect"
                  "ively fail a batch that was inside the one it was sold to.",
    )
    passed = models.BooleanField(null=True, blank=True, editable=False)
    instrument = models.ForeignKey(
        "quality.Instrument", null=True, blank=True, on_delete=models.PROTECT,
        related_name="readings", help_text="What it was measured on.")
    calibration = models.ForeignKey(
        "quality.Calibration", null=True, blank=True, on_delete=models.PROTECT,
        related_name="readings", editable=False,
        help_text="The calibration the instrument was under when this posted, frozen.")

    class Meta:
        ordering = ["inspection", "plan_line", "id"]

    def __str__(self):
        shown = self.value if self.value is not None else self.present
        return f"{self.plan_line.characteristic.code}: {shown}"

    def save(self, *args, **kwargs):
        if getattr(self, "_judging", False):
            super().save(*args, **kwargs)
            return
        if self.inspection.posted:
            raise ValidationError(
                f"Cannot change a reading on {self.inspection}, which is "
                "posted. Void it and take another."
            )
        if self.plan_line.plan_id != self.inspection.plan_id:
            raise ValidationError(
                f"{self.plan_line} is not a line of {self.inspection.plan}."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.inspection.posted:
            raise ValidationError(
                f"Cannot remove a reading from {self.inspection}, which is "
                "posted."
            )
        return super().delete(*args, **kwargs)


from .calibration import Calibration, Instrument  # noqa: E402,F401
