"""
A quality alert is what the plant found wrong before a customer did:
numbered, placed where it was seen, owned, with corrective actions
hanging off it as off a complaint. It closes with its root cause once
every action is done and the corrective ones checked; unlike a
complaint it may close with no action at all. Not a defect after all,
it is cancelled with the reason and kept.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from .alerts import AlertSeverity, AlertStatus, QualityAlert
from .complaints import ActionKind, CorrectiveAction, overdue_actions
from .models import Machine, WorkCentre
from .tests_complaints import ComplaintTestCase
from .tests_orders import TODAY


class AlertTestCase(ComplaintTestCase):
    def setUp(self):
        super().setUp()
        self.centre = WorkCentre.objects.create(code="LOOM-9", name="Loom shed 9")
        self.other = WorkCentre.objects.create(code="PRINT-9", name="Printing 9")
        self.machine = Machine.objects.create(code="L9-1", name="Loom 9-1", work_centre=self.centre)

    def alert(self, **extra):
        values = {"raised_on": TODAY, "title": "GSM low on loom 9-1", "severity": AlertSeverity.HIGH,
                  "machine": self.machine, "raised_by": self.qa, "owner": self.plant_head}
        values.update(extra)
        return QualityAlert.objects.create(**values)

    def action(self, alert, kind=ActionKind.CORRECTIVE, due=None):
        return CorrectiveAction.objects.create(alert=alert, kind=kind, description="Re-tension the warp",
                                               owner=self.qa, due_on=due or TODAY)


class RaisingTests(AlertTestCase):
    def test_numbered_and_placed_where_it_was_seen(self):
        alert = self.alert()
        self.assertEqual((alert.number[:3], alert.status, alert.work_centre, alert.where()),
                         ("QA-", AlertStatus.OPEN, self.centre, "L9-1"))
        with self.assertRaisesMessage(ValidationError, "is not in"):
            self.alert(work_centre=self.other)
        with self.assertRaisesMessage(ValidationError, "Say what was found"):
            self.alert(title="  ")
        with self.assertRaisesMessage(ValidationError, "nothing or more"):
            self.alert(quantity_affected=Decimal("-1"))

    def test_an_action_is_on_a_complaint_or_an_alert_one_or_the_other(self):
        alert = self.alert()
        complaint = self.complaint()
        with self.assertRaisesMessage(ValidationError, "one or the other"):
            CorrectiveAction.objects.create(alert=alert, complaint=complaint, kind=ActionKind.CORRECTIVE,
                                            description="both", owner=self.qa, due_on=TODAY)
        with self.assertRaisesMessage(ValidationError, "one or the other"):
            CorrectiveAction.objects.create(kind=ActionKind.CORRECTIVE, description="neither", owner=self.qa, due_on=TODAY)
        self.assertEqual(str(self.action(alert)), f"Corrective on {alert}: Re-tension the warp")


class ClosingTests(AlertTestCase):
    def test_closed_with_its_root_cause_once_the_actions_are_done_and_checked(self):
        alert = self.alert()
        action = self.action(alert)
        with self.assertRaisesMessage(ValidationError, "root cause"):
            alert.close("", self.qa)
        with self.assertRaisesMessage(ValidationError, "is not done"):
            alert.close("Warp tension drifted", self.qa)
        action.done("Re-tensioned", on_date=TODAY)
        with self.assertRaisesMessage(ValidationError, "whether it worked"):
            alert.close("Warp tension drifted", self.qa)
        action.verify(self.plant_head, on_date=TODAY)
        alert.close("Warp tension drifted", self.qa, on_date=TODAY)
        self.assertEqual((alert.status, alert.root_cause, alert.decided_by, alert.decided_on),
                         (AlertStatus.CLOSED, "Warp tension drifted", self.qa, TODAY))
        with self.assertRaisesMessage(ValidationError, "reopen it to change it"):
            alert.title = "edited after"
            alert.save()
        with self.assertRaisesMessage(ValidationError, "reopen it to change it"):
            self.action(alert)

    def test_an_alert_may_close_with_no_action_where_a_complaint_may_not(self):
        alert = self.alert(title="One roll off GSM, scrapped")
        alert.close("One-off: wrong bobbin loaded, roll scrapped", self.qa)
        self.assertEqual(alert.status, AlertStatus.CLOSED)

    def test_not_a_defect_is_said_why_and_kept_and_may_be_reopened(self):
        alert = self.alert()
        with self.assertRaisesMessage(ValidationError, "Say why"):
            alert.cancel(" ", self.qa)
        alert.cancel("Gauge was out of calibration, not the loom", self.qa)
        self.assertEqual((alert.status, alert.cancelled_reason[:5]), (AlertStatus.CANCELLED, "Gauge"))
        with self.assertRaisesMessage(ValidationError, "cancel it as not a defect"):
            alert.delete()
        with self.assertRaisesMessage(ValidationError, "Say why it is reopened"):
            alert.reopen("")
        alert.reopen("It recurred on the next doff")
        self.assertEqual((alert.status, alert.decided_by, alert.reopened_reason[:11]), (AlertStatus.OPEN, None, "It recurred"))

    def test_late_actions_and_old_open_alerts_are_counted(self):
        alert = self.alert(raised_on=TODAY - datetime.timedelta(days=20))
        late = self.action(alert, due=TODAY - datetime.timedelta(days=1))
        self.action(alert, due=TODAY + datetime.timedelta(days=5))
        self.assertEqual(overdue_actions(TODAY), [late])
        from apps.web.checks import _alerts_open

        self.assertEqual((_alerts_open(TODAY), _alerts_open(TODAY - datetime.timedelta(days=7))), (1, 0))


class OfficeTests(AlertTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_the_inspector_raises_it_and_acts_on_it_and_the_manager_decides(self):
        inspector = self.as_("Quality Inspector")
        raised = inspector.post("/api/manufacturing/quality-alerts/", {
            "raised_on": "2026-06-01", "title": "Seam lets go at 40 kg", "severity": "high",
            "machine": self.machine.pk, "owner": self.plant_head.pk, "quantity_affected": "120"}, format="json")
        self.assertEqual(raised.status_code, 201, raised.content)
        alert = raised.json()
        self.assertEqual((alert["number"][:3], alert["status"], alert["work_centre"], alert["where"], alert["owner_name"]),
                         ("QA-", "open", self.centre.pk, "L9-1", self.plant_head.party.name))
        added = inspector.post("/api/manufacturing/corrective-actions/", {
            "alert": alert["id"], "kind": "containment", "description": "Hold the batch", "owner": self.qa.pk,
            "due_on": "2026-06-02"}, format="json")
        self.assertEqual(added.status_code, 201, added.content)
        self.assertEqual(inspector.post(f"/api/manufacturing/corrective-actions/{added.json()['id']}/done/",
                                        {"note": "Held", "on_date": "2026-06-02"}, format="json").status_code, 200)
        seen = inspector.get(f"/api/manufacturing/quality-alerts/{alert['id']}/").json()
        self.assertEqual([(row["kind"], row["done_on"]) for row in seen["actions"]], [("containment", "2026-06-02")])
        closed = self.as_("Quality Manager").post(f"/api/manufacturing/quality-alerts/{alert['id']}/close/",
                                                  {"root_cause": "Thread tension", "by": self.qa.pk}, format="json")
        self.assertEqual((closed.status_code, closed.json()["status"], closed.json()["root_cause"]),
                         (200, "closed", "Thread tension"), closed.content)
        self.assertEqual(inspector.get("/api/manufacturing/quality-alerts/", {"status": "closed"}).json()[0]["id"], alert["id"])

    def test_who_may(self):
        supervisor = self.as_("Production Supervisor")
        # The day left out (the screen sends null) is today.
        raised = supervisor.post("/api/manufacturing/quality-alerts/", {"raised_on": None, "title": "Print off register",
                                                                        "work_centre": self.other.pk}, format="json")
        self.assertEqual((raised.status_code, raised.json()["raised_on"]), (201, str(timezone.localdate())), raised.content)
        self.assertEqual(supervisor.patch(f"/api/manufacturing/quality-alerts/{raised.json()['id']}/", {"title": "x"},
                                          format="json").status_code, 403)
        self.assertEqual(self.as_("Bookkeeper").get("/api/manufacturing/quality-alerts/").status_code, 403)
        cancelled = self.as_("Quality Manager").post(f"/api/manufacturing/quality-alerts/{raised.json()['id']}/cancel/",
                                                     {"reason": "Proof was wrong, not the press", "by": self.qa.pk}, format="json")
        self.assertEqual((cancelled.status_code, cancelled.json()["status"]), (200, "cancelled"), cancelled.content)
