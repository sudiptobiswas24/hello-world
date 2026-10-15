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

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.core import windows
from apps.core.approvals import authors, check_not_raised_by
from apps.core.models import (
    AuditModel,
    DocumentSequence,
    answer_to_its_document,
    correction_date,
    documents_it_answers_to,
    lock_rows,
    serialised,
    to_date,
)
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
    # A failure under an advisory plan: held, and the Quality Manager's to
    # decide (RISKS.md "Decisions taken", 22). Never chosen; posted as.
    AWAITING = "awaiting", "Awaiting the Quality Manager's decision"


# What the Quality Manager may decide an awaiting inspection as.
DECISIONS = (Disposition.CONCESSION, Disposition.REWORK, Disposition.REJECT)


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
    decided_at = models.DateTimeField(
        null=True, blank=True, editable=False,
        help_text="When the Quality Manager decided a failure that waited for them.")
    decision_login = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
        help_text="The login that decided a failure that waited: the first decision stands.")
    told = models.TextField(
        blank=True, editable=False,
        help_text="What posting a failure that waits said: who was told, and who was not "
                  "mailed. Kept as it was said, not worked out again from whoever holds "
                  "the role later.")
    escalated_on = models.DateField(
        null=True, blank=True, editable=False,
        help_text="The day a failure left undecided past the next working day went to "
                  "the administrator. Once per inspection.")
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
    failed_at_the_machines = models.BooleanField(
        default=False, editable=False,
        help_text="Booked off its line at the machines (a doff, a film roll, a bag bundle) outside "
                  "its gauge with a supervisor's PIN. A failure then waits for the Quality Manager "
                  "whatever the plan (decision 23): nobody concedes it on the spot.")
    recalled_by = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="recalls",
        editable=False,
        help_text="A recall (O207): the verdict on a batch this lot was made from that held it "
                  "after this lot's own release. It measures nothing; it waits for the Quality "
                  "Manager's decision on this lot.")

    class Meta:
        ordering = ["-inspected_on", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["number"], condition=~Q(number=""),
                name="inspection_number_unique",
            ),
        ]
        permissions = [("decide_inspection",
                        "Can decide a failure under an advisory plan (concession, rework or reject)")]

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
        if self.recalled_by_id is not None:
            # A recall is the system's, measured by nobody: nobody is barred from deciding it.
            return set()
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

    def _check_follows_the_standing_verdict(self):
        """
        A re-inspection is dated no earlier than the verdict it overturns.

        The latest verdict speaks for a batch. One dated before the
        verdict standing now cannot be the latest, so posted after it, it
        stood beside it unheard: passed on 1 June, a rejection dated 30 May
        was posted, read "rejected", and left the lot released. Refused
        rather than ranked by when it was posted, so that the order of the
        dates is the order of the decisions for every reader of them — the
        release gate, a certificate, the strength report — and voiding the
        latest hands the question back to the one dated before it. A
        retained sample tested again is measured, and dated, the day it is
        tested; which sample it was is the reading's own reference.

        Under a lock on the batch, so two inspections of it posting at once
        each see the other's date.

        The verdicts are the batch's own and those of every batch it was
        re-made from (O146): PP-A rejected on 1 June and split into PP-A2
        was passed by an inspection of PP-A2 dated 25 May, because only
        PP-A2's own inspections were read. And a failure waiting for the
        Quality Manager is theirs to decide: a second verdict beside it,
        passed or not, would decide it for them.
        """
        from .release import lot_and_sources, open_recall, standing_verdicts

        for lot in lot_and_sources(self.lot):
            waiting = open_recall(lot)
            if waiting is not None:
                raise ValidationError(
                    f"{waiting.number} recalled {lot} and waits for the Quality Manager's decision. "
                    f"Another verdict on {self.lot} now would decide it for them: they decide "
                    f"{waiting.number} first.")
        for lot, standing in standing_verdicts(self.lot, lock=True):
            if standing.pk == self.pk:
                continue
            if standing.disposition == Disposition.AWAITING:
                raise ValidationError(
                    f"{standing.number} failed {lot} on {standing.inspected_on} and waits for "
                    f"the Quality Manager's decision. Another verdict on {self.lot} now would "
                    f"decide it for them: they decide {standing.number}, or it is voided, first."
                )
            own = lot.pk == self.lot_id
            self.inspected_on = correction_date(
                self.inspected_on, standing.inspected_on,
                f"{self.lot} cannot be inspected {'again ' if own else ''}on",
                f"{standing.number} decided it" if own
                else f"{standing.number} decided {lot}, which it was made from,",
            )

    def _check_concession(self, by, awaited=False):
        """
        What a concession asks, posted so or decided later: a failure to
        concede, the person taking it (the login's own, never a name typed)
        and why, and a second person where the plant asks for one. A pass
        that waited (`awaited`: made from a batch quality holds) concedes
        what is inside it.
        """
        if self.result == Result.PASS and not awaited:
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
        # Asked last, so that each rule above still answers in its own words. A concession accepts a
        # failure, so it is a decision, and the Quality Manager's, whoever asks
        # (decisions 22 and 23; rereview 2): an Inspector posting the failing readings as a concession
        # released the batch, and every recall of what was made from it, with nobody deciding.
        if not self.decides(by):
            raise ValidationError(
                f"A concession takes {self.lot} as it is, out of specification: that is the Quality "
                "Manager's decision, whoever posts the readings. Post them as they came out, and the "
                "Quality Manager takes it by concession.")

    @staticmethod
    def decides(by):
        """Whether `by` is the Quality Manager's: who decides a failure, and the only one who lifts a hold."""
        return by is not None and by.has_perm("quality.decide_inspection")

    def check_lifted_by(self, by, ended="Voided", remedy="voids it, if it was taken wrong", reinspection=False):
        """
        Only the Quality Manager lifts a hold (decisions 22 and 23). The one rule asked by every door
        that ends this verdict, so that the next one cannot be written without it: voiding it, and
        posting over it a verdict that releases the batch (rereview 1). The review's F2 closed the
        void alone, and an Inspector lifted the same recall by posting a pass of the rejected batch.

        This verdict is the Quality Manager's to end when it waits for them, when they decided it,
        or when it holds its batch (rejected, held for rework). `reinspection`: the verdict ending it
        is a new reading of the batch, and a re-inspection after the rework the Quality Manager
        decided is what they asked for, so anyone who may post one posts it. A rework nobody decided
        (posted so outright) asked for nothing of theirs.
        """
        if self.decides(by):
            return
        shown = self.get_disposition_display().lower()
        if self.disposition == Disposition.AWAITING:
            raise ValidationError(
                f"{self} waits for the Quality Manager's decision. {ended}, {self.lot} would go with "
                f"nobody deciding: the Quality Manager {remedy}.")
        if reinspection and self.disposition == Disposition.REWORK and self.decided_at is not None:
            return
        if self.decided_at is not None:
            raise ValidationError(
                f"{self} was decided by the Quality Manager ({shown}). {ended}, their decision would be "
                f"undone: the Quality Manager {remedy}.")
        if self.disposition in (Disposition.REJECT, Disposition.REWORK):
            raise ValidationError(
                f"{self} holds {self.lot} ({shown}), and with it whatever was made from it. {ended}, the "
                f"hold would lift with nobody deciding: the Quality Manager {remedy}.")

    def waits_for_a_decision(self):
        """Posted, standing, and the Quality Manager's still to decide."""
        return self.posted and not self.is_voided() and self.disposition == Disposition.AWAITING

    def decision_due_on(self):
        """The next working day after it was posted: when the Quality Manager's to-do falls due."""
        from .decisions import next_working_day

        posted = timezone.localdate(self.posted_at) if self.posted_at else to_date(self.inspected_on)
        return next_working_day(posted)

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
        # What the plan says now, not what the caller's copy said: whether a
        # failure waits for the Quality Manager is decided from it here.
        self.plan = InspectionPlan.objects.get(pk=self.plan_id)
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
        if self.disposition == Disposition.AWAITING:
            raise ValidationError(
                "Awaiting the Quality Manager's decision is not chosen: it is what a "
                "failure under an advisory plan posts as. Leave the decision blank."
            )
        from .release import lot_and_sources, lots_made_from

        # The batch, what it was made from and everything made from it, in
        # one key order: what this verdict holds is recalled under the same
        # locks a second verdict on another source of it would take (O207).
        lock_rows(*lot_and_sources(self.lot), *lots_made_from(self.lot), refresh=False)
        self._check_follows_the_standing_verdict()
        self.result = self._judge()
        # A failure under an advisory plan is not rejected by the system: it
        # waits, held, for the Quality Manager (RISKS.md, decision 22), and
        # nobody decides it as it is posted, whoever posts it. Nor one booked
        # off its line at the machines, whatever the plan (decision 23).
        # A batch made at the machines from work off its limits further up the line (a roll, a
        # coating) waits however its own reading came out: that check had nothing else to hold.
        waits = ((self.result == Result.FAIL and not self.plan.is_mandatory)
                 or self.failed_at_the_machines)
        if waits and self.disposition:
            raise ValidationError(
                f"{self.lot} {'failed' if self.result == Result.FAIL else 'was made off its limits'} under {self.plan}"
                + (", off its line at the machines" if self.failed_at_the_machines else ", which is advisory")
                + ", so the Quality Manager decides what happens to it, not whoever posts the readings. "
                "Leave the decision blank and post: the batch is held until they decide."
            )
        # A pass of a batch made from one quality holds releases what is
        # inside it unmeasured: ten assemblies from rejected frames were
        # passed by an Inspector's ordinary reading of the assemblies. It
        # waits for the Quality Manager as a failure does, unless they post
        # it (review F3). Splits and joins of a held batch are the same.
        if not waits and self.result == Result.PASS and self.disposition in ("", Disposition.ACCEPT):
            from .release import held_inside

            if held_inside(self.lot) and not (by is not None and by.has_perm("quality.decide_inspection")):
                waits = True
                self.disposition = ""
        if waits:
            self.disposition = Disposition.AWAITING
            self.decided_by = None
        elif not self.disposition:
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
        from .release import RELEASING, latest_inspection

        # Released over a verdict that holds the batch, or over the Quality Manager's decision, the
        # hold lifts: theirs, as voiding it is (rereview 1). Their rework asked for this reading.
        overturned = latest_inspection(self.lot) if self.disposition in RELEASING else None
        if overturned is not None:
            overturned.check_lifted_by(by, ended="Released over it by a new verdict",
                                       remedy="posts the new verdict", reinspection=True)
        if self.disposition == Disposition.CONCESSION:
            self._check_concession(by)
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
        self.telling = None
        if waits:
            from .decisions import tell_on_posting

            # Who was told, and who was not mailed, is a fact of the posting:
            # kept as said, for the page and for whoever asks later.
            self.telling = tell_on_posting(self, by)
            self.told = self.telling.warning
            Inspection.objects.filter(pk=self.pk).update(told=self.told)
        else:
            from .release import lot_decided

            lot_decided(self.lot, by)
        from .recall import lift_when_committed, recall
        from .release import HOLDING

        # Held now, it holds what was made from it (O207); released, it may
        # free a recall of something made from it.
        self.recalled = recall(self, by) if self.disposition in HOLDING else []
        if self.disposition not in HOLDING:
            lift_when_committed(self.lot, f"{self.lot} was {self.get_disposition_display().lower()} "
                                          f"by {self.number}.")
        return self

    @serialised("posted", "voided_at", "disposition", "decided_at", "decision_login")
    def decide(self, by, disposition, note=""):
        """
        The Quality Manager's decision on a failure that waited for them:
        use as is (a concession they sign), rework, or reject, and why.

        Only a holder of `quality.decide_inspection`, and never whoever
        recorded the readings or raised the inspection, whatever the plant
        asks of a concession posted outright. The first decision stands:
        two pressed at once, the second waits for the first's lock and is
        told it was decided. Deciding closes everyone's to-do on it, as done
        by the decider. Not dated by hand: stamped as it is taken, so there
        is no day to argue with the verdict it follows.
        """
        if not self.posted or self.is_voided():
            raise ValidationError(f"{self} is {'voided' if self.posted else 'not posted'}: there is "
                                  "nothing to decide.")
        if self.disposition != Disposition.AWAITING:
            if self.decided_at is not None:
                who = self.decision_login.get_username() if self.decision_login_id else "someone"
                raise ValidationError(
                    f"{self} was decided already: {self.get_disposition_display().lower()}, by {who} "
                    f"at {timezone.localtime(self.decided_at):%d %b %Y %H:%M}. The first decision stands.")
            raise ValidationError(f"{self} is not waiting for a decision: it is "
                                  f"{self.get_disposition_display().lower()}.")
        if by is None or not by.has_perm("quality.decide_inspection"):
            raise ValidationError("The Quality Manager decides a failure under an advisory plan.")
        if self.recalled_by_id is not None:
            from .recall import recall_stands

            if not recall_stands(self):
                raise ValidationError(
                    f"{self} recalled {self.lot}, and nothing inside it is held any more: its own "
                    "release stands again, so there is nothing to decide.")
        if disposition not in DECISIONS:
            raise ValidationError("Decide it as one of: use as is (a concession), rework, or reject.")
        if not isinstance(note, str):
            raise ValidationError("Say why in words: the note is text.")
        note = " ".join(note.split())
        if not note:
            raise ValidationError("Say why: the decision is what is asked about when the batch is argued over.")
        limit = Inspection._meta.get_field("decision_note").max_length
        if len(note) > limit:
            raise ValidationError(f"Say why in at most {limit} characters; this is {len(note)}. "
                                  "Nothing is cut: shorten it, and put the rest in a note on the inspection.")
        check_not_raised_by(by, self.raised_by(), self)
        self.disposition, self.decision_note = disposition, note
        try:
            if disposition == Disposition.CONCESSION:
                self._check_concession(by, awaited=True)
            else:
                employee = getattr(by, "employee", None)
                self.decided_by = employee.party if employee is not None else None
        except ValidationError:
            self.refresh_from_db(fields=["disposition", "decision_note", "decided_by"])
            raise
        self.decided_at = timezone.now()
        self.decision_login = by
        super().save(update_fields=["disposition", "decision_note", "decided_by", "decided_at",
                                    "decision_login", "updated_at"])
        from .decisions import close_to_dos
        from .release import lot_decided

        # Those who hold something on the batch first (a receipt, an off roll's to-do), whose
        # to-dos were given before this one's: to-dos are closed in the order they were given.
        lot_decided(self.lot, by)
        close_to_dos(self, by, f"{self} decided: {self.get_disposition_display().lower()}. {note}")
        if disposition == Disposition.CONCESSION:
            from apps.core.chatter import Note
            from django.contrib.contenttypes.models import ContentType

            from .recall import lift_when_committed
            from .release import still_held_words

            lift_when_committed(self.lot, f"{self.lot} was taken as is by {self.number}.")
            # Taken as is over what it was decided on, never over a hold it did not show (review
            # F4): a batch inside it still held keeps it held, and the decision says so.
            self.still_held = still_held_words(self.lot)
            if self.still_held:
                Note.objects.create(content_type=ContentType.objects.get_for_model(Inspection),
                                    object_id=self.pk, created_by=by, updated_by=by,
                                    body=f"Taken as is by {self.number}, and still held: {self.still_held}")
        return self

    @serialised("posted", "voided_at", "disposition", "escalated_on")
    def escalate(self, today=None):
        """
        Left undecided past the next working day: to the administrator,
        once. Run again, it finds the day recorded and writes nothing.
        Returns who was told, or None when there was nothing to do.
        """
        today = to_date(today) or timezone.localdate()
        if not self.waits_for_a_decision() or self.escalated_on is not None:
            return None
        if today <= self.decision_due_on():
            return None
        if self.recalled_by_id is not None:
            from .recall import recall_stands

            if not recall_stands(self):
                # Lifted, and its lift not yet written: nothing is overdue.
                self.void("The recall is lifted: nothing inside the batch is held any more.")
                return None
        from .decisions import tell_the_administrator

        told = tell_the_administrator(self, today)
        if told.told:
            # Recorded only when somebody was told: one that reached nobody
            # is tried again on the next run, and says so again.
            self.escalated_on = today
            super().save(update_fields=["escalated_on", "updated_at"])
        return told

    @serialised("posted", "voided_at", "disposition")
    def void(self, reason="", by=None):
        """
        Undo a posting.

        The batch's status is whatever its latest standing inspection
        says, so voiding this one hands the question back to the one
        before it — or to nobody, which is 'not yet inspected' and not
        'passed'. One still waiting for the Quality Manager closes their
        to-dos and everyone else's on it: there is nothing left to decide.
        `by` is who voided it, for those to-dos.

        Voiding one that waits takes the hold off with nobody deciding, and
        voiding one the Quality Manager decided undoes their decision: an
        Inspector voided a failure and posted a pass, and the batch went with
        nobody deciding (review Q01). Both are the Quality Manager's
        (`quality.decide_inspection`), as the decision is. From code naming
        nobody (a station withdrawing a batch with its output) it is not
        asked: the batch goes with it.
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
        # Only the Quality Manager lifts a hold (decisions 22 and 23, review F2): the rule a verdict
        # posted over this one asks too (check_lifted_by). A pass may be voided by whoever may void:
        # that leaves the batch not yet inspected, which holds it, and lifts nothing.
        if by is not None:
            self.check_lifted_by(by)
        self.voided_at = timezone.now()
        self.voided_reason = reason
        super().save(update_fields=["voided_at", "voided_reason", "updated_at"])
        if self.disposition == Disposition.AWAITING:
            from .decisions import close_to_dos

            close_to_dos(self, by, f"{self} was voided, so there is nothing to decide: {reason}")
        from .recall import lift_when_committed, refile

        # The verdict gone, a recall it made may have nothing left to hold (O207); and the batch
        # itself, still held by what is inside it, is never left with nothing to decide (review F6).
        lift_when_committed(self.lot, f"{self.number} was voided: {reason}")
        refile(self.lot, by)
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

    def delete(self, *args, **kwargs):
        """
        A posted inspection is not deleted (O10): it is what holds or
        releases its batch, and gone, the batch reads never inspected, its
        readings go by cascade and its to-dos stay on nothing. Voided, with
        a reason, it stays as the record of what was measured.
        """
        if Inspection.objects.filter(pk=self.pk, posted=True).exists():
            raise ValidationError(
                f"{self} is posted: it is what holds or releases {self.lot}. Void it, with a "
                "reason, rather than delete it.")
        return super().delete(*args, **kwargs)


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

    def locked_before_it(self):
        """The inspections it leaves and joins, as save() holds them (apps.core.models.answer_to_its_document)."""
        return documents_it_answers_to(self, "inspection")

    @transaction.atomic
    def save(self, *args, **kwargs):
        if getattr(self, "_judging", False):
            super().save(*args, **kwargs)
            return
        # Shared rule B: the inspection it leaves and the one it joins, both held. Asked of the one
        # it joined alone, a reading was moved off a posted inspection into a draft.
        answer_to_its_document(
            self, "inspection",
            lambda inspection: f"Cannot change a reading on {inspection}, which is posted. Void it and take another.")
        if self.plan_line.plan_id != self.inspection.plan_id:
            raise ValidationError(
                f"{self.plan_line} is not a line of {self.inspection.plan}."
            )
        super().save(*args, **kwargs)

    @transaction.atomic
    def delete(self, *args, **kwargs):
        answer_to_its_document(
            self, "inspection", lambda inspection: f"Cannot remove a reading from {inspection}, which is posted.")
        return super().delete(*args, **kwargs)


from .calibration import Calibration, Instrument  # noqa: E402,F401
