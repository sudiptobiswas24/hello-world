"""review_cost probes: O5/O6 statutory files read what the month's slips kept."""

import datetime
from decimal import Decimal

from .statutory_files import ecr_rows, esi_rows
from .tests_statutory_files import FilesTestCase
from .tests_statutory import JUNE


class FiledMonthKeptTests(FilesTestCase):
    def test_pf_base_list_edited_after_the_run_does_not_restate_the_ecr(self):
        """
        C: basic 9,000, HRA 3,000. PF (12%) is taken of basic + special: PF wages 9,000, PF 1,080. After June is
        posted HRA is added to what PF is taken of; June's file still says 9,000 (the slip deducted 1,080).
        """
        c = self.person("C", basic="9000", hra="3000")
        c.uan = "100200300402"
        # The ECR asks every member's date of birth: the pension share stops after 58.
        from .pay_details import EmployeePayDetails

        EmployeePayDetails.objects.create(employee=c, date_of_birth=datetime.date(1990, 1, 1))
        c.save()
        run = self.run_for(*JUNE)
        before = {row["name"]: row["epf_wages"] for row in ecr_rows(run)}
        self.assertEqual(before["C"], 9000)
        self.pf.base_components.add(self.hra)
        after = {row["name"]: row["epf_wages"] for row in ecr_rows(run)}
        self.assertEqual(after["C"], 9000)

    def test_a_component_re_marked_after_the_run_does_not_restate_which_line_is_pf(self):
        """PF re-marked as not statutory after June: June's ECR still lists the members it filed."""
        from .payroll import Statutory

        run = self.run_for(*JUNE)
        names = [row["name"] for row in ecr_rows(run)]
        self.pf.statutory = Statutory.NONE
        self.pf.save()
        self.assertEqual([row["name"] for row in ecr_rows(run)], names)

    def test_esi_wages_base_list_edited_after_the_run(self):
        """A: ESI wages are all four components (22,300). Dropping OT from ESI's base after June must not restate."""
        run = self.run_for(*JUNE)
        [row] = esi_rows(run)
        before = row["wages"]
        self.esi.base_components.remove(self.ot)
        [row] = esi_rows(run)
        self.assertEqual(row["wages"], before)

