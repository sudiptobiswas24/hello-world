"""
The supplier's certificate of analysis, read before a bought-in batch is released (L28).

Polymer PP-1 came in from V-1 on 5 January 2026 (GRN). The plan in force
that day asks for a certificate and reads it against MFI 2.8 to 3.6 and
ash at most 0.050. The plan is advisory, so nothing but the certificate
decides whether PP-1 may be issued. Expected outcomes worked in
/home/user/work/calc_fix_coa/scenarios.py. Refusals first, each on its
own build; then what is done, and undone.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import Party, PartyRole, PartyRoleAssignment, UnitOfMeasure
from apps.hr.models import Employee
from apps.inventory.models import Lot, TrackingMode
from apps.quality.certificates import SupplierCertificate, SupplierCertificateLine
from apps.quality.models import (
    Characteristic,
    Disposition,
    Inspection,
    InspectionPlan,
    PlanLine,
    Reading,
    ReleaseStatus,
)
from apps.quality.release import check_released, release_status, supplied_by, why_held

from .models import GoodsReceipt, GoodsReceiptLine
from .tests_lifecycle import PurchasingLifecycleTestCase

RECEIVED = datetime.date(2026, 1, 5)
RECORDED = datetime.date(2026, 1, 6)


class CertificateTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.item.tracking = TrackingMode.LOT
        self.item.save()
        self.lot = Lot.objects.create(item=self.item, code="PP-1")
        order = self.make_order("10", "5")
        self.receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=RECEIVED)
        GoodsReceiptLine.objects.create(receipt=self.receipt, order_line=order.lines.first(), lot=self.lot,
                                        warehouse=self.warehouse, quantity_received=Decimal("10"))
        self.receipt.post()
        g10 = UnitOfMeasure.objects.create(code="g10", name="g/10 min")
        pct = UnitOfMeasure.objects.create(code="pct", name="Per cent")
        self.mfi = Characteristic.objects.create(code="MFI", name="Melt flow index", uom=g10)
        self.ash = Characteristic.objects.create(code="ASH", name="Ash", uom=pct)
        self.plan = InspectionPlan.objects.create(item=self.item, is_mandatory=False, certificate_required=True)
        self.mfi_line = PlanLine.objects.create(plan=self.plan, characteristic=self.mfi, lower_limit=Decimal("2.8"),
                                                upper_limit=Decimal("3.6"), line_number=1)
        PlanLine.objects.create(plan=self.plan, characteristic=self.ash, upper_limit=Decimal("0.050"), line_number=2)
        self.users, self.clients = {}, {}
        for name, role in (("meera", "Quality Inspector"), ("asha", "Quality Manager"),
                           ("sunil", "Stores Manager"), ("pavan", "Purchasing Clerk")):
            user = User.objects.create_user(name)
            user.groups.add(Group.objects.get(name=role))
            party = Party.objects.create(code=name.upper(), name=name.title())
            PartyRoleAssignment.objects.create(party=party, role=PartyRole.EMPLOYEE)
            Employee.objects.create(party=party, employee_number=name.upper(),
                                    hire_date=datetime.date(2020, 1, 1), user=user)
            self.users[name] = user
            self.clients[name] = APIClient()
            self.clients[name].force_authenticate(user)

    def certificate(self, values, lot=None, vendor=None, post=True, by="meera"):
        """A certificate of `values` {characteristic: value}, posted by `by` unless told not to."""
        certificate = SupplierCertificate.objects.create(
            lot=lot or self.lot, vendor=vendor or self.vendor, supplier_number="RIL/COA/7781",
            issued_on=RECEIVED, recorded_on=RECORDED)
        for characteristic, value in values.items():
            SupplierCertificateLine.objects.create(certificate=certificate, characteristic=characteristic,
                                                   value=Decimal(value))
        if post:
            certificate.post(by=self.users[by])
        return certificate

    def passed_inspection(self):
        inspection = Inspection.objects.create(lot=self.lot, plan=self.plan, inspected_on=timezone.localdate())
        for line in self.plan.lines.all():
            Reading.objects.create(inspection=inspection, plan_line=line,
                                   value=Decimal("3.2") if line.characteristic == self.mfi else Decimal("0.02"))
        return inspection


class CertificateRefusalTests(CertificateTestCase):
    def test_a_certificate_from_another_vendor_is_refused(self):
        other = Party.objects.create(code="V-2", name="Another supplier")
        with self.assertRaises(ValidationError) as caught:
            self.certificate({self.mfi: "3.2", self.ash: "0.02"}, vendor=other, post=False)
        self.assertIn("came in from V-1 - Supplier on GRN-2026-00001", str(caught.exception))
        response = self.clients["meera"].post("/api/quality/supplier-certificates/", {
            "lot": self.lot.pk, "vendor": other.pk, "supplier_number": "X-1", "issued_on": "2026-01-05",
            "recorded_on": "2026-01-06"}, format="json")
        self.assertEqual(response.status_code, 400, response.content)

    def test_a_lot_made_here_takes_no_certificate(self):
        made_here = Lot.objects.create(item=self.item, code="PP-HOME")
        self.assertIsNone(supplied_by(made_here))
        certificate = self.certificate({}, lot=made_here, post=False)
        with self.assertRaises(ValidationError) as caught:
            certificate.post(by=self.users["meera"])
        self.assertIn("was not received from a vendor", str(caught.exception))
        # Nor a value on it: asked as soon as it can be known.
        with self.assertRaises(ValidationError):
            SupplierCertificateLine.objects.create(certificate=certificate, characteristic=self.mfi,
                                                   value=Decimal("3.2"))
        # And the gate asks nothing of it.
        self.assertEqual(why_held(self.item, made_here), "")

    def test_a_second_standing_certificate_is_refused_until_the_first_is_voided(self):
        first = self.certificate({self.mfi: "3.2", self.ash: "0.02"})
        second = self.certificate({self.mfi: "3.3", self.ash: "0.02"}, post=False)
        with self.assertRaises(ValidationError) as caught:
            second.post(by=self.users["meera"])
        self.assertIn(f"{first.number} already stands for WDG-1 / PP-1", str(caught.exception))
        first.void("Typed against the wrong lot.", on_date=RECORDED, by=self.users["meera"])
        second.post(by=self.users["meera"])
        self.assertTrue(second.posted)

    def test_a_value_for_a_characteristic_the_plan_does_not_name_is_refused(self):
        density = Characteristic.objects.create(code="DENS", name="Density",
                                                uom=UnitOfMeasure.objects.create(code="gcc", name="g/cc"))
        certificate = self.certificate({self.mfi: "3.2", self.ash: "0.02"}, post=False)
        with self.assertRaises(ValidationError) as caught:
            SupplierCertificateLine.objects.create(certificate=certificate, characteristic=density,
                                                   value=Decimal("0.905"))
        self.assertIn("does not name DENS - Density", str(caught.exception))
        # Written past the line's own check, post() refuses it too.
        line = SupplierCertificateLine(certificate=certificate, characteristic=density, value=Decimal("0.905"))
        line._judging = True
        line.save()
        with self.assertRaises(ValidationError) as caught:
            certificate.post(by=self.users["meera"])
        self.assertIn("does not name DENS", str(caught.exception))

    def test_a_void_dated_before_the_certificate_was_recorded_is_refused(self):
        certificate = self.certificate({self.mfi: "3.2", self.ash: "0.02"})
        with self.assertRaises(ValidationError) as caught:
            certificate.void("Wrong lot.", on_date=datetime.date(2026, 1, 4), by=self.users["meera"])
        self.assertIn("cannot be voided on 2026-01-04: it was recorded on 2026-01-06", str(caught.exception))
        certificate.refresh_from_db()
        self.assertIsNone(certificate.voided_at)


class CertificateDecidesReleaseTests(CertificateTestCase):
    def test_without_a_certificate_the_lot_is_held_and_says_why(self):
        said = why_held(self.item, self.lot)
        self.assertIn("PP-1 came in from V-1 - Supplier on GRN-2026-00001", said)
        self.assertIn("None is recorded", said)
        self.assertEqual(release_status(self.lot), ReleaseStatus.HELD)
        with self.assertRaises(ValidationError):
            check_released(self.item, self.lot, action="issue")

    def test_a_certificate_within_lets_the_lot_go(self):
        certificate = self.certificate({self.mfi: "3.2", self.ash: "0.020"})
        self.assertEqual(certificate.result, "within")
        self.assertEqual(why_held(self.item, self.lot), "")
        # The edge of the limits is inside them.
        certificate.void("Re-issued by the supplier.", by=self.users["meera"])
        edge = self.certificate({self.mfi: "3.6", self.ash: "0.050"})
        self.assertEqual(edge.result, "within")

    def test_a_certificate_outside_holds_the_lot_naming_the_value(self):
        certificate = self.certificate({self.mfi: "4.1", self.ash: "0.020"})
        self.assertEqual(certificate.result, "outside")
        said = why_held(self.item, self.lot, action="issue")
        self.assertIn(f"{certificate.number}, the certificate RIL/COA/7781 from V-1 - Supplier, is outside specification "
                      "(MFI 4.1 against 2.8 to 3.6)", said)
        self.assertIn("It cannot issue until the Quality Manager takes it by concession", said)

    def test_a_value_left_out_is_missing_and_not_within(self):
        certificate = self.certificate({self.mfi: "3.2"})
        self.assertEqual(certificate.result, "outside")
        missing = certificate.lines.get(characteristic=self.ash)
        self.assertEqual((missing.value, missing.within, missing.upper_limit), (None, False, Decimal("0.050")))
        self.assertIn("(ASH missing)", why_held(self.item, self.lot))

    def test_the_plan_edited_after_posting_does_not_move_the_verdict(self):
        certificate = self.certificate({self.mfi: "4.1", self.ash: "0.020"})
        PlanLine.objects.filter(pk=self.mfi_line.pk).update(upper_limit=Decimal("4.5"))
        certificate.refresh_from_db()
        line = certificate.lines.get(characteristic=self.mfi)
        self.assertEqual((certificate.result, line.upper_limit, line.within), ("outside", Decimal("3.6"), False))
        self.assertIn("MFI 4.1 against 2.8 to 3.6", why_held(self.item, self.lot))

    def test_a_pass_alone_does_not_take_a_lot_whose_certificate_is_outside(self):
        self.certificate({self.mfi: "4.1", self.ash: "0.020"})
        inspection = self.passed_inspection()
        self.clients["meera"].post(f"/api/quality/inspections/{inspection.pk}/post/")
        inspection.refresh_from_db()
        self.assertEqual((inspection.result, inspection.disposition), ("pass", Disposition.AWAITING))
        self.assertIn("but its supplier's certificate", why_held(self.item, self.lot))
        # Not even the Quality Manager's own pass: they take it by concession, or not at all.
        inspection.void("Posted to test.", by=self.users["asha"])
        accepted = self.passed_inspection()
        accepted.disposition = Disposition.ACCEPT
        accepted.save()
        accepted.post(by=self.users["asha"])
        self.assertEqual(accepted.disposition, Disposition.AWAITING)
        self.assertNotEqual(why_held(self.item, self.lot), "")

    def test_the_quality_managers_concession_releases_it(self):
        self.certificate({self.mfi: "4.1", self.ash: "0.020"})
        inspection = self.passed_inspection()
        inspection.post(by=self.users["meera"])
        response = self.clients["asha"].post(f"/api/quality/inspections/{inspection.pk}/decide/", {
            "disposition": "concession", "note": "MFI 4.1 runs on line 2 at a lower draw."}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(why_held(self.item, self.lot), "")

    def test_a_concession_taken_before_the_certificate_does_not_concede_it(self):
        inspection = self.passed_inspection()
        inspection.post(by=self.users["meera"])
        # Passed, but the certificate came after: and it is outside.
        Inspection.objects.filter(pk=inspection.pk).update(disposition=Disposition.CONCESSION,
                                                           decided_at=timezone.now() - datetime.timedelta(hours=1))
        self.certificate({self.mfi: "4.1", self.ash: "0.020"})
        self.assertIn("is outside specification", why_held(self.item, self.lot))

    def test_voiding_hands_the_question_back(self):
        certificate = self.certificate({self.mfi: "3.2", self.ash: "0.020"})
        self.assertEqual(why_held(self.item, self.lot), "")
        response = self.clients["meera"].post(f"/api/quality/supplier-certificates/{certificate.pk}/void/",
                                              {"reason": "Supplier withdrew it."}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("None is recorded", why_held(self.item, self.lot))
        certificate.refresh_from_db()
        self.assertEqual(certificate.voided_on, timezone.localdate())

    def test_a_plan_that_asks_for_no_certificate_holds_nothing(self):
        InspectionPlan.objects.filter(pk=self.plan.pk).update(certificate_required=False)
        self.assertEqual(why_held(self.item, self.lot), "")

    def test_a_return_to_the_vendor_leaves_the_certificate_as_a_record(self):
        certificate = self.certificate({self.mfi: "4.1", self.ash: "0.020"})
        self.receipt.create_return(debit_bills=False)
        certificate.refresh_from_db()
        self.assertTrue(certificate.posted)
        self.assertIsNone(certificate.voided_at)
        self.assertEqual(supplied_by(self.lot).reference, self.receipt.number)


class CertificateOutsideTellsTheQualityManagerTests(CertificateTestCase):
    """O432: an outside certificate was posted without a word to the Quality Manager."""

    def to_dos(self, certificate, **kwargs):
        from django.contrib.contenttypes.models import ContentType

        from apps.core.chatter import FollowUp

        return FollowUp.objects.filter(content_type=ContentType.objects.get_for_model(SupplierCertificate),
                                       object_id=certificate.pk, **kwargs)

    def test_a_certificate_outside_gives_the_quality_manager_a_to_do(self):
        certificate = self.certificate({self.mfi: "4.1", self.ash: "0.020"})
        to_do = self.to_dos(certificate).get()
        self.assertEqual((to_do.assigned_to, to_do.done_on, to_do.link),
                         (self.users["asha"], None, f"/quality/supplier-certificates/{certificate.pk}"))
        self.assertIn(f"Decide {certificate.number}: WDG-1 / PP-1 is outside specification (MFI 4.1 against 2.8 to 3.6)",
                      to_do.summary)
        self.assertIn("held until the Quality Manager takes it by concession", to_do.note)
        # The Quality Manager reads it on their own list.
        mine = self.clients["asha"].get("/api/core/follow-ups/", {"mine": "true"})
        self.assertEqual(mine.status_code, 200, mine.content)
        self.assertIn(to_do.pk, [row["id"] for row in mine.json()])

    def test_a_certificate_within_gives_nobody_a_to_do(self):
        certificate = self.certificate({self.mfi: "3.2", self.ash: "0.020"})
        self.assertFalse(self.to_dos(certificate).exists())

    def test_voiding_it_closes_the_to_do(self):
        certificate = self.certificate({self.mfi: "4.1", self.ash: "0.020"})
        response = self.clients["meera"].post(f"/api/quality/supplier-certificates/{certificate.pk}/void/",
                                              {"reason": "Supplier withdrew it."}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        to_do = self.to_dos(certificate).get()
        self.assertEqual(to_do.done_on, timezone.localdate())
        self.assertIn("voided, so there is nothing to decide: Supplier withdrew it.", to_do.outcome)

    def test_the_concession_answers_the_to_do(self):
        certificate = self.certificate({self.mfi: "4.1", self.ash: "0.020"})
        inspection = self.passed_inspection()
        inspection.post(by=self.users["meera"])
        response = self.clients["asha"].post(f"/api/quality/inspections/{inspection.pk}/decide/", {
            "disposition": "concession", "note": "MFI 4.1 runs on line 2 at a lower draw."}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        to_do = self.to_dos(certificate).get()
        self.assertEqual((to_do.done_on, to_do.done_by), (timezone.localdate(), self.users["asha"]))
        self.assertIn(f"taken by concession on {inspection.number}", to_do.outcome)

    def test_under_a_plan_that_asks_for_none_the_quality_manager_reads_it_and_answers_it(self):
        """
        O444. MFI 4.1 against 2.8 to 3.6 under a plan that does not ask for a certificate: the batch is
        not held, and the to-do is for the Quality Manager to read before it is used. Asha, to whom it
        is addressed, answers it from her own list, and the to-do says that is how it is answered.
        """
        InspectionPlan.objects.filter(pk=self.plan.pk).update(certificate_required=False)
        certificate = self.certificate({self.mfi: "4.1", self.ash: "0.020"})
        self.assertEqual(why_held(self.item, self.lot), "")
        to_do = self.to_dos(certificate).get()
        self.assertEqual(to_do.assigned_to, self.users["asha"])
        self.assertIn("does not ask for a certificate, so it holds nothing", to_do.note)
        self.assertIn("mark this done with what was decided", to_do.note)
        mine = self.clients["asha"].get("/api/core/follow-ups/", {"mine": "true"}).json()
        self.assertEqual([row["may_change"] for row in mine if row["id"] == to_do.pk], [True])
        answered = self.clients["asha"].post(f"/api/core/follow-ups/{to_do.pk}/done/",
                                             {"outcome": "Read; line 2 told to run it at a lower draw."},
                                             format="json")
        self.assertEqual(answered.status_code, 200, answered.content)
        to_do.refresh_from_db()
        self.assertEqual((to_do.done_on, to_do.done_by), (timezone.localdate(), self.users["asha"]))


class APageOfCertifiedBatchesTests(CertificateTestCase):
    def test_a_page_of_outside_batches_reads_their_certificates_once(self):
        """
        O434: the batches page read each outside lot's certificate lines for its words (verdict_words), a
        query per lot. Read with the page, 30 such lots cost what 10 do.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        order = self.make_order("40", "5")
        line = order.lines.first()

        def outside(count, start):
            receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=RECEIVED)
            lots = [Lot.objects.create(item=self.item, code=f"PP-{number:03d}") for number in range(start, start + count)]
            for lot in lots:
                GoodsReceiptLine.objects.create(receipt=receipt, order_line=line, lot=lot, warehouse=self.warehouse,
                                                quantity_received=Decimal("1"))
            receipt.post()
            for lot in lots:
                self.certificate({self.mfi: "4.1", self.ash: "0.020"}, lot=lot)

        def page():
            with CaptureQueriesContext(connection) as asked:
                response = self.clients["sunil"].get("/api/inventory/lots/", {"item": self.item.pk, "page_size": 50})
            self.assertEqual(response.status_code, 200, response.content)
            return response.json(), len(asked)

        outside(10, 100)
        few_rows, few = page()
        outside(20, 200)
        rows, many = page()
        print(f"\nO434 the batches page: {few} queries for {len(few_rows)} batches, {many} for {len(rows)}")
        said = {row["code"]: row["held_because"] for row in rows}
        self.assertIn("is outside specification (MFI 4.1 against 2.8 to 3.6)", said["PP-215"])
        self.assertEqual(len(rows), 31)
        self.assertLessEqual(many, few + 2, f"{few} queries for {len(few_rows)} batches, {many} for {len(rows)}")


class PostedCertificateTests(CertificateTestCase):
    def test_a_posted_certificate_refuses_edits_and_delete(self):
        certificate = self.certificate({self.mfi: "4.1", self.ash: "0.020"})
        certificate.supplier_number = "RIL/COA/7782"
        with self.assertRaises(ValidationError):
            certificate.save()
        with self.assertRaises(ValidationError):
            SupplierCertificate.objects.get(pk=certificate.pk).delete()
        line = certificate.lines.get(characteristic=self.mfi)
        line.value = Decimal("3.2")
        with self.assertRaises(ValidationError):
            line.save()
        with self.assertRaises(ValidationError):
            line.delete()
        manager = self.clients["asha"]
        self.assertEqual(manager.patch(f"/api/quality/supplier-certificates/{certificate.pk}/",
                                       {"supplier_number": "X"}, format="json").status_code, 400)
        self.assertEqual(manager.delete(f"/api/quality/supplier-certificates/{certificate.pk}/").status_code, 400)
        self.assertEqual(manager.patch(f"/api/quality/supplier-certificate-lines/{line.pk}/",
                                       {"value": "3.2"}, format="json").status_code, 400)

    def test_quality_records_and_posts_through_the_api_and_purchasing_reads(self):
        meera = self.clients["meera"]
        created = meera.post("/api/quality/supplier-certificates/", {
            "lot": self.lot.pk, "vendor": self.vendor.pk, "supplier_number": "RIL/COA/7781",
            "issued_on": "2026-01-05", "recorded_on": "2026-01-06"}, format="json")
        self.assertEqual(created.status_code, 201, created.content)
        pk = created.json()["id"]
        for characteristic, value in ((self.mfi, "3.2"), (self.ash, "0.020")):
            # As the screen's adder sends it: the present-or-absent box unticked beside the number.
            added = meera.post("/api/quality/supplier-certificate-lines/", {
                "certificate": pk, "characteristic": characteristic.pk, "value": value, "present": False},
                format="json")
            self.assertEqual(added.status_code, 201, added.content)
        posted = meera.post(f"/api/quality/supplier-certificates/{pk}/post/")
        self.assertEqual(posted.status_code, 200, posted.content)
        self.assertEqual((posted.json()["result"], posted.json()["lot_held_because"]), ("within", ""))
        clerk = self.clients["pavan"]
        listed = clerk.get("/api/quality/supplier-certificates/", {"lots": str(self.lot.pk)})
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([row["supplier_number"] for row in listed.json()], ["RIL/COA/7781"])
        self.assertEqual(clerk.post(f"/api/quality/supplier-certificates/{pk}/void/",
                                    {"reason": "x"}, format="json").status_code, 403)
        self.assertEqual(self.clients["sunil"].get("/api/quality/supplier-certificates/").status_code, 403)
