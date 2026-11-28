"""
The supplier's certificate of analysis for a batch bought in (L28).

A polymer supplier sends, with every lot, a certificate: its melt flow
index, its ash, its density, as their laboratory measured them. The plant
reads it against its own plan for the item before the lot goes into a run.

Three decisions (g26 brief):

**Read against the plan of the day it came in.** The plan in force on the
receipt's date (release.plan_for), and that plan's limits are copied onto
each line as the certificate posts. Whether it was within is a fact of that
day (mistake 4): a plan edited next month does not move it.

**It holds; it never releases.** Where the plan asks for a certificate, a
lot without one standing, or with one outside or missing a value, is held
(release.certificate_words). One within decides nothing more: the batch
still needs whatever its own inspection needs. Only the Quality Manager's
concession on an inspection takes a lot whose certificate is outside.

**Posted is immutable.** A wrong certificate is voided, dated by
correction_date(), and another recorded; whether a lot is held is derived
from the standing one, so the void hands the question back.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import (
    AuditModel,
    DocumentSequence,
    answer_to_its_document,
    correction_date,
    documents_it_answers_to,
    lock_rows,
    serialised,
    takes_back,
    to_date,
)
from apps.inventory.models import Lot


class CertificateResult(models.TextChoices):
    WITHIN = "within", "Within specification"
    OUTSIDE = "outside", "Outside specification"


class SupplierCertificate(AuditModel):
    """One supplier's certificate of analysis for one received lot."""

    number = models.CharField(max_length=32, blank=True, editable=False)
    lot = models.ForeignKey(
        Lot, on_delete=models.PROTECT, related_name="supplier_certificates",
        help_text="The batch it certifies: one the plant received from this vendor.")
    vendor = models.ForeignKey(
        "core.Party", on_delete=models.PROTECT, related_name="supplier_certificates",
        help_text="Who issued it: the vendor of the receipt that brought the batch in.")
    supplier_number = models.CharField(
        max_length=64, help_text="The certificate's own number, as the supplier printed it.")
    issued_on = models.DateField(help_text="The day the supplier dated it.")
    recorded_on = models.DateField(
        help_text="The day quality read it in. Its void is dated no earlier.")
    plan = models.ForeignKey(
        "quality.InspectionPlan", null=True, blank=True, on_delete=models.PROTECT,
        related_name="supplier_certificates", editable=False,
        help_text="The plan it was read against: the one in force on the receipt's day, kept as it posted.")
    result = models.CharField(
        max_length=8, choices=CertificateResult.choices, blank=True, editable=False,
        help_text="Within only when every line of the plan has a value inside its limits, as they stood.")
    posted = models.BooleanField(default=False, editable=False)
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        editable=False)
    voided_on = models.DateField(null=True, blank=True, editable=False)
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_reason = models.CharField(max_length=255, blank=True, editable=False)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        editable=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-recorded_on", "-id"]
        constraints = [
            # One standing certificate a lot: the second post of two at once is refused here
            # too, should anything write past post()'s lock.
            models.UniqueConstraint(
                fields=["lot"], condition=Q(posted=True, voided_at__isnull=True),
                name="one_standing_supplier_certificate_per_lot"),
            models.UniqueConstraint(
                fields=["number"], condition=~Q(number=""), name="supplier_certificate_number_unique"),
        ]

    def __str__(self):
        return self.number or f"Draft certificate for {self.lot}"

    def is_voided(self):
        return self.voided_at is not None

    def is_within(self):
        return self.result == CertificateResult.WITHIN

    def verdict_words(self):
        """'outside specification (MFI 4.1 against 2.8 to 3.6; ASH missing)'."""
        if self.is_within():
            return "within specification"
        said = []
        # The page's lines where the page read them (release._prefill_certificates, O434), else its own.
        lines = (self.lines.all() if "lines" in getattr(self, "_prefetched_objects_cache", {})
                 else self.lines.select_related("characteristic"))
        for line in sorted(lines, key=lambda line: line.pk):
            if line.within is False:
                said.append(f"{line.characteristic.code} {line.shown_value()} against {line.limit_words()}"
                            if not line.is_missing() else f"{line.characteristic.code} missing")
        return "outside specification" + (f" ({'; '.join(said)})" if said else "")

    def held_for_post(self):
        """The batch, held before the certificate: two posting for one lot at once, the second waits."""
        return [self.lot]

    def plan_in_force(self):
        """The plan this certificate is read against, or a refusal in words (release.certificate_plan)."""
        from .release import certificate_plan, supplied_by

        supply = supplied_by(self.lot)
        if supply is None:
            raise ValidationError(
                f"{self.lot} was not received from a vendor (made here, or a customer's return): "
                "there is no supplier whose certificate it could take.")
        plan = certificate_plan(self.lot)
        if plan is None:
            raise ValidationError(
                f"No inspection plan for {self.lot.item} was in force on {supply.received_on}, when "
                f"{supply.reference} brought {self.lot} in: there is nothing to read the certificate against.")
        return plan

    def check_vendor(self):
        from .release import supplied_by

        supply = supplied_by(self.lot)
        if supply is not None and supply.vendor.pk != self.vendor_id:
            raise ValidationError(
                f"{self.lot} came in from {supply.vendor} on {supply.reference}. A certificate from "
                f"{self.vendor} says nothing about it.")

    @serialised("posted", "voided_at", held_first="held_for_post")
    def post(self, by=None):
        """Read each value against the plan of the receipt's day, freeze the limits, and post."""
        if self.posted:
            raise ValidationError(f"{self} is already posted.")
        plan = self.plan_in_force()
        self.check_vendor()
        today = timezone.localdate()
        self.recorded_on = to_date(self.recorded_on) or today
        self.issued_on = to_date(self.issued_on)
        if self.recorded_on > today or self.issued_on > today:
            raise ValidationError(f"{self} is dated after today: nothing has been certified or read on a "
                                  "day that has not happened.")
        if self.issued_on > self.recorded_on:
            raise ValidationError(f"{self} would be read in on {self.recorded_on}, before the supplier "
                                  f"dated it on {self.issued_on}.")
        if not self.supplier_number.strip():
            raise ValidationError("Give the supplier's certificate number: it is what the supplier is asked about.")
        lock_rows(self.lot, refresh=False)
        standing = SupplierCertificate.objects.filter(
            lot_id=self.lot_id, posted=True, voided_at__isnull=True).exclude(pk=self.pk).first()
        if standing is not None:
            raise ValidationError(
                f"{standing.number} already stands for {self.lot}. Void it, with a reason, before "
                "another certificate is recorded for the batch.")
        planned = {line.characteristic_id: line for line in plan.lines.select_related("characteristic")}
        lines = list(self.lines.select_related("characteristic"))
        unplanned = [line.characteristic.code for line in lines if line.characteristic_id not in planned]
        if unplanned:
            raise ValidationError(
                f"{plan} does not name {', '.join(unplanned)}: a value for something the plan does not "
                "check decides nothing. Take the line off.")
        if not planned:
            raise ValidationError(f"{plan} names nothing to check: there is nothing to read the certificate against.")
        given = {line.characteristic_id: line for line in lines}
        judged = []
        for characteristic_id, plan_line in planned.items():
            line = given.get(characteristic_id)
            if line is None:
                # A characteristic the plan names and the certificate left out: missing, not within.
                line = SupplierCertificateLine(certificate=self, characteristic=plan_line.characteristic)
            line.judge(plan_line)
            line._judging = True
            line.save()
            judged.append(line)
        # What it says now is what was judged here, not lines a caller read before (a prefetch).
        getattr(self, "_prefetched_objects_cache", {}).pop("lines", None)
        self.plan = plan
        self.result = (CertificateResult.WITHIN
                       if all(line.within for line in judged) else CertificateResult.OUTSIDE)
        if not self.number:
            self.number = DocumentSequence.next_for(
                "quality.suppliercertificate", self.recorded_on, name="Supplier certificates", prefix="COA-")
        self.posted, self.posted_at, self.posted_by = True, timezone.now(), by
        super().save(update_fields=["number", "recorded_on", "issued_on", "plan", "result", "posted",
                                    "posted_at", "posted_by", "updated_at"])
        if not self.is_within():
            self._tell_the_quality_manager(by)
        from .release import lot_decided

        lot_decided(self.lot, by)
        return self

    def _tell_the_quality_manager(self, by):
        """
        A certificate outside: the Quality Manager's to-do, through the to-do every
        held batch gives them (decisions.tell_the_quality_manager). Posted without a
        word, they learnt of it only when an inspection of the lot waited (O432). Closed
        when it is voided, or when the batch is taken by concession (certificate_answered).

        Under a plan that does not ask for a certificate it is still raised (O444): the
        batch is not held, so no concession will ever answer it, but a supplier saying
        the batch is out of specification is exactly what the Quality Manager is to read
        before it is used, and dropping it would put back O432. It is answered by the
        person it is addressed to, marking it done with what they decided; it says so.
        """
        from .decisions import tell_the_quality_manager
        from .release import certificate_failed

        verdict = self.verdict_words()
        if certificate_failed(self.lot) is not None:
            summary = f"Decide {self.number}: {self.lot} is {verdict} and is held"
            body = (f"{self.number}, the certificate {self.supplier_number} from {self.vendor}, says {self.lot} "
                    f"({self.lot.item}) is {verdict}. {self.plan} asks for a certificate, so the batch is held "
                    "until the Quality Manager takes it by concession on an inspection, or the certificate "
                    "is voided.")
        else:
            summary = f"{self.number}: {self.lot} is {verdict}"
            body = (f"{self.number}, the certificate {self.supplier_number} from {self.vendor}, says {self.lot} "
                    f"({self.lot.item}) is {verdict}. {self.plan} does not ask for a certificate, so it holds "
                    "nothing and nothing in the system decides it: for the Quality Manager to read before "
                    "the batch is used, and to mark this done with what was decided (O444).")
        tell_the_quality_manager(self, summary, link=f"/quality/supplier-certificates/{self.pk}", by=by, body=body)

    @serialised("posted", "voided_at", held_first="held_for_post")
    @takes_back
    def void(self, reason="", on_date=None, by=None):
        """
        Withdraw it. The lot goes back to having no certificate: held again
        where its plan requires one. Never before the day it was recorded.
        """
        if not self.posted:
            raise ValidationError(f"{self} is not posted.")
        if self.is_voided():
            raise ValidationError(f"{self} is already voided.")
        reason = " ".join((reason or "").split())
        if not reason:
            raise ValidationError("Voiding a certificate needs a reason: the supplier will ask why.")
        self.voided_on = correction_date(on_date, self.recorded_on, f"{self} cannot be voided on",
                                         "it was recorded")
        self.voided_at, self.voided_reason, self.voided_by = timezone.now(), reason, by
        super().save(update_fields=["voided_on", "voided_at", "voided_reason", "voided_by", "updated_at"])
        from .decisions import close_to_dos

        close_to_dos(self, by, f"{self} was voided, so there is nothing to decide: {reason}")
        return self

    @transaction.atomic
    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            if SupplierCertificate.objects.filter(pk=self.pk, posted=True).exists():
                raise ValidationError(f"Cannot modify {self} once it is posted. Void it and record another.")
        if self.lot_id and self.vendor_id is None:
            # Whoever sold the plant the batch: the one vendor its certificate can come from.
            from .release import supplied_by

            supply = supplied_by(self.lot)
            if supply is None:
                self.plan_in_force()  # refuses in words: not received from a vendor
            self.vendor = supply.vendor
        if self.lot_id and self.vendor_id:
            self.check_vendor()
        if self.recorded_on is None:
            self.recorded_on = timezone.localdate()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if SupplierCertificate.objects.filter(pk=self.pk, posted=True).exists():
            raise ValidationError(
                f"{self} is posted: it is what decided whether {self.lot} could go. Void it, with a "
                "reason, rather than delete it.")
        return super().delete(*args, **kwargs)


def certificate_answered(lot, by=None):
    """
    Registered with the release gate (register_lot_decided): `lot` was decided. A
    standing certificate outside that the Quality Manager has now taken by concession
    is answered, and its to-do closes with the inspection that conceded it (O432).
    """
    from .decisions import close_to_dos
    from .release import certificate_conceded, latest_inspection

    certificate = SupplierCertificate.objects.filter(
        lot=lot, posted=True, voided_at__isnull=True, result=CertificateResult.OUTSIDE).first()
    if certificate is not None and certificate_conceded(lot, certificate):
        close_to_dos(certificate, by, f"{lot} taken by concession on {latest_inspection(lot).number}.")


class SupplierCertificateLine(AuditModel):
    """One value the supplier certified, and the limits it was read against when it posted."""

    certificate = models.ForeignKey(SupplierCertificate, on_delete=models.CASCADE, related_name="lines")
    characteristic = models.ForeignKey(
        "quality.Characteristic", on_delete=models.PROTECT, related_name="supplier_certificate_lines")
    value = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True,
        help_text="For a measured characteristic. Blank on a line the plan names and the supplier left out.")
    present = models.BooleanField(null=True, blank=True, help_text="For a present-or-absent characteristic.")
    lower_limit = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True, editable=False)
    upper_limit = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True, editable=False,
        help_text="The plan's limits as they stood when the certificate posted, frozen.")
    within = models.BooleanField(
        null=True, blank=True, editable=False,
        help_text="Inside the frozen limits. False on a missing value.")

    class Meta:
        ordering = ["certificate", "id"]
        constraints = [
            models.UniqueConstraint(fields=["certificate", "characteristic"],
                                    name="one_certificate_line_per_characteristic"),
        ]

    def __str__(self):
        return f"{self.characteristic.code}: {self.shown_value()}"

    def is_missing(self):
        return self.value is None and self.present is None

    def shown_value(self):
        if self.value is not None:
            return format(self.value.normalize(), "f")
        return "missing" if self.present is None else ("present" if self.present else "absent")

    def limit_words(self):
        shown = [format(limit.normalize(), "f") if limit is not None else None
                 for limit in (self.lower_limit, self.upper_limit)]
        if shown[0] is not None and shown[1] is not None:
            return f"{shown[0]} to {shown[1]}"
        return f"at least {shown[0]}" if shown[0] is not None else f"at most {shown[1]}"

    def judge(self, plan_line):
        """Freeze `plan_line`'s limits here and say whether the value is inside them."""
        self.lower_limit, self.upper_limit = plan_line.lower_limit, plan_line.upper_limit
        if self.characteristic.is_measured():
            self.within = self.value is not None and plan_line.passes(self.value)
        else:
            if self.value is not None:
                raise ValidationError(f"{self.characteristic} is present or absent, not a number.")
            self.within = bool(self.present)

    def locked_before_it(self):
        return documents_it_answers_to(self, "certificate")

    @transaction.atomic
    def save(self, *args, **kwargs):
        # A measured characteristic is a number: a present-or-absent box left unticked beside it (the
        # screen's adder sends one) says nothing, and is not kept.
        if self.characteristic.is_measured():
            self.present = None
        if getattr(self, "_judging", False):
            super().save(*args, **kwargs)
            return
        answer_to_its_document(
            self, "certificate",
            lambda certificate: f"Cannot change a line of {certificate}, which is posted. Void it and record another.")
        # Asked as soon as it can be known (the plan of the receipt's day); asked again as it posts.
        plan = self.certificate.plan_in_force()
        if not plan.lines.filter(characteristic_id=self.characteristic_id).exists():
            raise ValidationError(
                f"{plan} does not name {self.characteristic}: a value for something the plan does not "
                "check decides nothing.")
        super().save(*args, **kwargs)

    @transaction.atomic
    def delete(self, *args, **kwargs):
        answer_to_its_document(
            self, "certificate", lambda certificate: f"Cannot remove a line from {certificate}, which is posted.")
        return super().delete(*args, **kwargs)
