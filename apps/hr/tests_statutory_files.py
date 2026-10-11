"""
June's files from the posted run in tests_statutory's plant:

  A: basic 12,000 + special 3,000 = PF wages 15,000; gross 22,300.
     EPF 1,800 (12%); EPS 8.33% of 15,000 = 1,249.50 -> 1,250; the
     employer's 1,800 less that: 550. ESI wages 22,300, all 22 working
     days of June paid.
  B: basic 20,000, capped to 15,000 PF wages; gross 28,000; same
     contributions; not covered by ESI, so not in that file.
"""

import datetime

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from rest_framework.test import APIClient

from .payroll import Statutory
from .statutory_files import ecr_rows, ecr_text, esi_csv, esi_rows
from .tests_statutory import JUNE, StatutoryTestCase


class FilesTestCase(StatutoryTestCase):
    def setUp(self):
        super().setUp()
        for component, kind in ((self.pf, Statutory.PF), (self.pf_er, Statutory.PF_EMPLOYER),
                                (self.esi, Statutory.ESI), (self.esi_er, Statutory.ESI_EMPLOYER),
                                (self.pt, Statutory.PT)):
            component.statutory = kind
            component.save()
        self.a.uan, self.a.esi_number = "100200300400", "3100200300"
        self.a.save()
        self.b.uan = "100200300401"
        self.b.save()


class EcrTests(FilesTestCase):
    def test_one_line_a_member_in_whole_rupees(self):
        run = self.run_for(*JUNE)
        self.assertEqual(ecr_rows(run), [
            {"uan": "100200300400", "name": "A", "gross": 22300, "epf_wages": 15000, "eps_wages": 15000,
             "edli_wages": 15000, "epf": 1800, "eps": 1250, "epf_diff": 550, "ncp_days": 0, "refund": 0},
            {"uan": "100200300401", "name": "B", "gross": 28000, "epf_wages": 15000, "eps_wages": 15000,
             "edli_wages": 15000, "epf": 1800, "eps": 1250, "epf_diff": 550, "ncp_days": 0, "refund": 0},
        ])
        self.assertEqual(ecr_text(run), "100200300400#~#A#~#22300#~#15000#~#15000#~#15000#~#1800#~#1250#~#550#~#0#~#0\r\n"
                                        "100200300401#~#B#~#28000#~#15000#~#15000#~#15000#~#1800#~#1250#~#550#~#0#~#0\r\n")

    def test_a_named_pension_component_is_taken_as_it_is(self):
        self.pf_er.statutory = Statutory.EPS
        self.pf_er.save()
        run = self.run_for(*JUNE)
        row = ecr_rows(run)[0]
        self.assertEqual((row["eps"], row["epf_diff"]), (1800, 0))

    def test_what_stops_the_file(self):
        self.b.uan = ""
        self.b.save()
        run = self.run_for(*JUNE)
        with self.assertRaisesMessage(ValidationError, "No UAN on B"):
            ecr_text(run)
        with self.assertRaisesMessage(ValidationError, "Only a posted run is filed"):
            ecr_text(self.run_for(datetime.date(2026, 7, 1), datetime.date(2026, 7, 31), post=False))
        self.a.uan = "12AB"
        with self.assertRaisesMessage(ValidationError, "twelve digits"):
            self.a.save()


class EsiTests(FilesTestCase):
    def test_only_the_insured_with_their_days_and_wages(self):
        run = self.run_for(*JUNE)
        self.assertEqual(esi_rows(run), [
            {"ip": "3100200300", "name": "A", "days": 22, "wages": 22300, "reason": 0, "last_day": ""}])
        lines = esi_csv(run).splitlines()
        self.assertTrue(lines[0].startswith("IP Number (10 Digits),IP Name"))
        self.assertEqual(lines[1], "3100200300,A,22,22300,0,")

    def test_a_leaver_carries_their_last_day(self):
        self.a.termination_date = datetime.date(2026, 6, 19)
        self.a.employment_status = "terminated"
        self.a.save()
        run = self.run_for(*JUNE)
        [row] = esi_rows(run)
        self.assertEqual((row["days"], row["last_day"]), (15, "19/06/2026"))


class FilesApiTests(FilesTestCase):
    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_taken_off_the_posted_run_by_whoever_reads_payslips(self):
        call_command("setup_roles", verbosity=0)
        run = self.run_for(*JUNE)
        officer = self.as_("Payroll Officer")
        ecr = officer.get(f"/api/hr/pay-runs/{run.pk}/ecr/")
        self.assertEqual((ecr.status_code, ecr["Content-Disposition"]), (200, 'attachment; filename="ECR_202606.txt"'))
        self.assertTrue(ecr.content.decode().startswith("100200300400#~#A#~#22300"))
        esi = officer.get(f"/api/hr/pay-runs/{run.pk}/esi/")
        self.assertEqual((esi.status_code, esi["Content-Disposition"]), (200, 'attachment; filename="ESI_202606.csv"'))
        self.assertEqual(self.as_("Sales Rep").get(f"/api/hr/pay-runs/{run.pk}/ecr/").status_code, 403)
        draft = self.run_for(datetime.date(2026, 7, 1), datetime.date(2026, 7, 31), post=False)
        self.assertEqual(officer.get(f"/api/hr/pay-runs/{draft.pk}/ecr/").status_code, 400)
