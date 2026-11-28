"""
What the tape line ran at. TAPE-A came off a run on EXT-1; the floor
recorded 6.50:1 at the start and 6.20:1 after the tape began to split.
The customer's strength complaint on TAPE-A shows both, and not the
settings of a run that made something else.

Recorded on a released or closed run, on a machine that run uses, and
never edited or deleted: a change is a new row.
"""

from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from .demand import genealogy
from .machines import Machine
from .models import WorkCentre, WorkOrder
from .tape_settings import TapeRunSetting
from .tests_complaints import ComplaintTestCase
from .tests_orders import TODAY


class TapeSettingsTestCase(ComplaintTestCase):
    def setUp(self):
        super().setUp()
        self.run_ = genealogy(self.tape_lot, 1)[0]["made_by"]
        self.line = Machine.objects.create(work_centre=self.loom, code="EXT-1A")

    def setting(self, run=None, ratio="6.50", **extra):
        return TapeRunSetting.objects.create(work_order=run or self.run_, draw_ratio=Decimal(ratio),
                                             quench_temperature_c=Decimal("28.0"), **extra)


class RefusedTests(TapeSettingsTestCase):
    def test_not_on_a_run_that_is_not_on_the_floor(self):
        draft = self.order("500")
        with self.assertRaisesMessage(ValidationError, "has been released"):
            self.setting(draft)
        WorkOrder.objects.filter(pk=draft.pk).update(status="cancelled")
        draft.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "has been released"):
            self.setting(draft)

    def test_not_on_a_machine_the_run_does_not_use(self):
        elsewhere = Machine.objects.create(
            work_centre=WorkCentre.objects.create(code="LOOM-9", name="Looms"), code="CL-9")
        with self.assertRaisesMessage(ValidationError, "CL-9 is not on a line this run uses"):
            self.setting(machine=elsewhere)
        self.assertEqual(self.setting(machine=self.line).machine, self.line)

    def test_a_draw_ratio_stretches(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.setting(ratio="1.00")

    def test_kept_as_recorded(self):
        setting = self.setting()
        setting.draw_ratio = Decimal("6.20")
        with self.assertRaisesMessage(ValidationError, "record the change as a new one"):
            setting.save()
        with self.assertRaisesMessage(ValidationError, "it stays"):
            setting.delete()
        setting.refresh_from_db()
        self.assertEqual(setting.draw_ratio, Decimal("6.50"))

    def test_on_a_closed_run_too(self):
        # The shift sheet is often entered after the run is closed.
        WorkOrder.objects.filter(pk=self.run_.pk).update(status="closed")
        self.run_.refresh_from_db()
        self.assertEqual(self.setting().work_order, self.run_)


class InTheInvestigationTests(TapeSettingsTestCase):
    def test_a_complaint_reads_what_its_batch_was_run_at(self):
        self.setting(ratio="6.50")
        self.setting(ratio="6.20", note="Tape splitting at the winder")
        other = self.order("500")
        other.release(TODAY)
        self.setting(other, ratio="7.00")
        complaint = self.complaint()
        complaint.add_lot(self.tape_lot)
        found = complaint.investigation()["tape_settings"]
        self.assertEqual([(row.work_order, row.draw_ratio) for row in found],
                         [(self.run_, Decimal("6.50")), (self.run_, Decimal("6.20"))])


class TapeSettingsApiTests(TapeSettingsTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_the_floor_records_and_quality_reads(self):
        supervisor = self.as_("Production Supervisor")
        made = supervisor.post("/api/manufacturing/tape-run-settings/", {
            "work_order": self.run_.pk, "machine": self.line.pk, "draw_ratio": "6.20",
            "quench_temperature_c": "28.5", "oven_temperature_c": "145.0"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        url = f"/api/manufacturing/tape-run-settings/{made.json()['id']}/"
        # Refused at the permission before the method: nobody holds change or delete on it.
        self.assertEqual(supervisor.patch(url, {"draw_ratio": "6.00"}, format="json").status_code, 403)
        self.assertEqual(supervisor.delete(url).status_code, 403)
        self.assertEqual(TapeRunSetting.objects.get().draw_ratio, Decimal("6.20"))

        inspector = self.as_("Quality Inspector")
        [row] = inspector.get("/api/manufacturing/tape-run-settings/", {"work_order": self.run_.pk}).json()
        self.assertEqual((row["machine_code"], row["draw_ratio"], row["quench_temperature_c"]),
                         ("EXT-1A", "6.20", "28.5"))
        refused = inspector.post("/api/manufacturing/tape-run-settings/", {
            "work_order": self.run_.pk, "draw_ratio": "6.20", "quench_temperature_c": "28"}, format="json")
        self.assertEqual(refused.status_code, 403)

        stretched_nothing = supervisor.post("/api/manufacturing/tape-run-settings/", {
            "work_order": self.run_.pk, "draw_ratio": "1", "quench_temperature_c": "28"}, format="json")
        self.assertEqual(stretched_nothing.status_code, 400)

        complaint = self.complaint()
        complaint.add_lot(self.tape_lot)
        found = self.as_("Quality Manager").get(
            f"/api/manufacturing/complaints/{complaint.pk}/investigation/").json()
        self.assertEqual([(row["run"], row["machine"], row["draw_ratio"]) for row in found["tape_settings"]],
                         [(self.run_.number, "EXT-1A", "6.20")])
