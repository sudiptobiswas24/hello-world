"""
Expense claims, appraisals and recruitment, as the people who use them.

Riley spends 300 on travel and 150 on meals and claims it; Jordan, who
manages Riley, approves; the books pay it from cash as one journal,
Dr travel 300 / Dr meals 150 / Cr cash 450, and reverse it when paid
from the wrong drawer. Jordan appraises Riley; Riley reads it only once
submitted and acknowledges it. An opening for one loom operator takes
two applicants; the one offered is hired and becomes the employee on
the rolls, and the opening is filled.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounting.models import Account, AccountType, JournalLine
from apps.core.models import Company, Currency, PartyRole, PartyRoleAssignment

from .appraisals import Appraisal, AppraisalStatus
from .expenses import ClaimStatus, ExpenseClaim, ExpenseLine
from .models import Department, Employee
from .recruitment import Applicant, JobOpening, OpeningStatus, Stage
from .tests import make_employee_party

JUNE_1 = datetime.date(2026, 6, 1)


class PeopleTestCase(TestCase):
    def setUp(self):
        call_command("setup_roles", verbosity=0)
        self.usd = Currency.objects.create(code="USD", name="USD", is_base=True)
        Company.objects.create(name="Test Co", base_currency=self.usd)
        self.travel = Account.objects.create(code="6200", name="Travel", account_type=AccountType.EXPENSE)
        self.meals = Account.objects.create(code="6210", name="Meals", account_type=AccountType.EXPENSE)
        self.cash = Account.objects.create(code="1000", name="Cash", account_type=AccountType.ASSET, holds_money=True)
        self.weaving = Department.objects.create(code="WEAVE", name="Weaving")
        self.manager = self.person("E006", "Jordan Park", "Line Manager")
        self.riley = self.person("E005", "Riley Chen", "Employee Self Service", manager=self.manager)
        self.other = self.person("E007", "Sam Lee", "Employee Self Service")
        self.hr = self.login("HR Admin")
        self.books = self.login("Bookkeeper")

    def login(self, role, username=None):
        user = User.objects.create_user(username or role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        return user

    def person(self, number, name, role, manager=None):
        user = self.login(role, username=number.lower())
        employee = Employee.objects.create(party=make_employee_party(number, name), employee_number=number,
                                           hire_date=datetime.date(2025, 1, 1), user=user, manager=manager,
                                           department=self.weaving)
        return employee

    @staticmethod
    def as_(user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def claim(self, employee=None, lines=(("Train to Pune", "travel", "300"), ("Lunch", "meals", "150"))):
        claim = ExpenseClaim.objects.create(employee=employee or self.riley, claim_date=JUNE_1, purpose="Customer visit, Pune")
        for description, account, amount in lines:
            ExpenseLine.objects.create(claim=claim, spent_on=JUNE_1, expense_account=getattr(self, account),
                                       description=description, amount=Decimal(amount))
        return claim


class ClaimTests(PeopleTestCase):
    def approved(self):
        claim = self.claim()
        claim.submit()
        claim.approve(self.manager)
        return claim

    def test_not_paid_from_an_expense_account(self):
        claim = self.approved()
        with self.assertRaisesMessage(ValidationError, "6200 - Travel is not a bank, cash or card account"):
            claim.pay(self.travel)
        claim.refresh_from_db()
        self.assertEqual((claim.status, claim.journal_entry, claim.paid_from), (ClaimStatus.APPROVED, None, None))

    def test_not_paid_from_what_customers_owe(self):
        receivable = Account.objects.create(code="1100", name="AR", account_type=AccountType.ASSET)
        with self.assertRaisesMessage(ValidationError, "1100 - AR is not a bank, cash or card account"):
            self.approved().pay(receivable)

    def test_paid_by_the_company_card(self):
        card = Account.objects.create(code="2300", name="Company card", account_type=AccountType.LIABILITY,
                                      holds_money=True)
        claim = self.approved()
        claim.pay(card, on_date=datetime.date(2026, 6, 3))
        lines = {(row.account.code, row.debit, row.credit) for row in JournalLine.objects.filter(entry=claim.journal_entry)}
        self.assertEqual(lines, {("6200", Decimal("300"), Decimal("0")), ("6210", Decimal("150"), Decimal("0")),
                                 ("2300", Decimal("0"), Decimal("450"))})

    def test_through_the_api_the_bookkeeper_is_told_why(self):
        claim = self.approved()
        refused = self.as_(self.books).post(f"/api/hr/expense-claims/{claim.pk}/pay/", {"paid_from": self.travel.pk},
                                             format="json")
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertIn("is not a bank, cash or card account", str(refused.content))
        self.assertEqual(ExpenseClaim.objects.get(pk=claim.pk).status, ClaimStatus.APPROVED)

    def paid_on_the_3rd(self):
        claim = self.approved()
        claim.pay(self.cash, on_date=datetime.date(2026, 6, 3))
        return claim

    def test_a_payment_is_not_reversed_before_it_was_made(self):
        claim = self.paid_on_the_3rd()
        with self.assertRaisesMessage(ValidationError, "is not unpaid on 2026-06-02: it was paid on 2026-06-03."):
            claim.unpay("Wrong drawer", on_date=datetime.date(2026, 6, 2))
        self.assertEqual(ExpenseClaim.objects.get(pk=claim.pk).status, ClaimStatus.PAID)

    def test_the_bookkeeper_does_not_reverse_a_payment_on_a_day_to_come(self):
        claim = self.paid_on_the_3rd()
        later = timezone.localdate() + datetime.timedelta(days=30)
        refused = self.as_(self.books).post(f"/api/hr/expense-claims/{claim.pk}/unpay/",
                                            {"reason": "Wrong drawer", "on_date": later.isoformat()}, format="json")
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertIn("that day has not come", str(refused.content))
        self.assertEqual(ExpenseClaim.objects.get(pk=claim.pk).status, ClaimStatus.PAID)

    def test_a_payment_dated_ahead_is_reversed_on_its_own_day_and_no_other(self):
        # Paid with a date three days ahead, it could be reversed neither before that day nor
        # on it, which had not come; it now cancels on that day, the only one it stands on.
        claim = self.approved()
        ahead = timezone.localdate() + datetime.timedelta(days=3)
        claim.pay(self.cash, on_date=ahead)
        with self.assertRaisesMessage(ValidationError, f"it was paid on {ahead}"):
            claim.unpay("Wrong drawer", on_date=timezone.localdate())
        later = ahead + datetime.timedelta(days=1)
        with self.assertRaisesMessage(ValidationError, f"is not unpaid on {later}: that day has not come"):
            claim.unpay("Wrong drawer", on_date=later)
        self.assertEqual(ExpenseClaim.objects.get(pk=claim.pk).status, ClaimStatus.PAID)
        claim.unpay("Wrong drawer")
        claim.refresh_from_db()
        self.assertEqual((claim.status, claim.voided_entry.date), (ClaimStatus.APPROVED, ahead))
        self.assertEqual(sum((line.debit - line.credit for line in JournalLine.objects.filter(account=self.cash)),
                             Decimal("0")), Decimal("0"))

    def test_submitted_decided_by_the_manager_and_paid_as_one_journal(self):
        claim = self.claim()
        self.assertEqual((claim.total(), claim.number, claim.status), (Decimal("450"), "", ClaimStatus.DRAFT))
        with self.assertRaisesMessage(ValidationError, "only an approved claim is paid"):
            claim.pay(self.cash)
        claim.submit()
        self.assertEqual((claim.number[:4], claim.status), ("EXP-", ClaimStatus.SUBMITTED))
        with self.assertRaisesMessage(ValidationError, "what is submitted is what is decided"):
            ExpenseLine.objects.create(claim=claim, spent_on=JUNE_1, expense_account=self.meals, description="Tea", amount=1)
        with self.assertRaisesMessage(ValidationError, "Nobody decides their own claim"):
            claim.approve(self.riley)
        with self.assertRaisesMessage(ValidationError, "does not manage"):
            claim.approve(self.other)
        claim.approve(self.manager, note="Agreed")
        self.assertEqual((claim.status, claim.decided_by, claim.decision_note), (ClaimStatus.APPROVED, self.manager, "Agreed"))
        claim.pay(self.cash, on_date=datetime.date(2026, 6, 3))
        self.assertEqual((claim.status, claim.paid_on, claim.paid_from), (ClaimStatus.PAID, datetime.date(2026, 6, 3), self.cash))
        lines = {(row.account.code, row.debit, row.credit) for row in JournalLine.objects.filter(entry=claim.journal_entry)}
        self.assertEqual(lines, {("6200", Decimal("300"), Decimal("0")), ("6210", Decimal("150"), Decimal("0")),
                                 ("1000", Decimal("0"), Decimal("450"))})
        self.assertTrue(claim.journal_entry.posted)
        with self.assertRaisesMessage(ValidationError, "a claim is changed while it is a draft"):
            claim.purpose = "edited"
            claim.save()
        # The reverse: paid from the wrong drawer, reversed, approved again, paid right.
        claim.unpay("Paid from petty cash, should be bank", on_date=datetime.date(2026, 6, 4))
        self.assertEqual((claim.status, claim.voided_entry.posted, claim.decision_note[:16]), (ClaimStatus.APPROVED, True, "Payment reversed"))
        self.assertEqual(JournalLine.objects.filter(entry=claim.voided_entry, account=self.cash, debit=Decimal("450")).count(), 1)
        # Approved again, it reads as unpaid: no date or drawer left from the payment reversed.
        claim.refresh_from_db()
        self.assertEqual((claim.paid_on, claim.paid_from), (None, None))
        bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True)
        claim.pay(bank, on_date=datetime.date(2026, 6, 5))
        self.assertEqual((claim.status, claim.paid_on, claim.paid_from), (ClaimStatus.PAID, datetime.date(2026, 6, 5), bank))
        balances = {account.code: sum((row.debit - row.credit for row in JournalLine.objects.filter(
            account=account, entry__posted=True)), Decimal("0")) for account in (self.cash, bank, self.travel)}
        self.assertEqual(balances, {"1000": Decimal("0"), "1010": Decimal("-450"), "6200": Decimal("300")})
        # Reversed a second time, the claim keeps only the latest reversal; the first belongs to
        # nothing, and reversed from the journal it would pay the claim from petty cash again.
        first_reversal = claim.voided_entry
        claim.unpay("Bank account closed", on_date=datetime.date(2026, 6, 6))
        self.assertIsNone(first_reversal.recorded_by())
        with self.assertRaisesMessage(ValidationError, "not by reversing its reversal"):
            first_reversal.reverse_by_hand()

    def test_what_is_refused(self):
        empty = ExpenseClaim.objects.create(employee=self.riley, claim_date=JUNE_1, purpose="Nothing")
        with self.assertRaisesMessage(ValidationError, "claims nothing"):
            empty.submit()
        with self.assertRaisesMessage(ValidationError, "more than nothing"):
            ExpenseLine.objects.create(claim=empty, spent_on=JUNE_1, expense_account=self.meals, description="Tea", amount=0)
        claim = self.claim()
        claim.submit()
        with self.assertRaisesMessage(ValidationError, "Say why"):
            claim.reject(self.manager, " ")
        claim.reject(self.manager, "No receipts")
        with self.assertRaisesMessage(ValidationError, "it is rejected, not deleted"):
            claim.delete()
        # HR decides anyone's; the manager's reports include the reports' reports.
        again = self.claim()
        again.submit()
        again.approve(self.other, as_hr=True)
        self.assertEqual(again.status, ClaimStatus.APPROVED)

    def test_the_claimant_their_manager_and_the_books_over_the_api(self):
        riley = self.as_(self.riley.user)
        made = riley.post("/api/hr/expense-claims/", {"purpose": "Customer visit"}, format="json")
        self.assertEqual((made.status_code, made.json()["employee"], made.json()["claim_date"]),
                         (201, self.riley.pk, str(timezone.localdate())), made.content)
        claim_id = made.json()["id"]
        for_other = riley.post("/api/hr/expense-claims/", {"purpose": "x", "employee": self.other.pk}, format="json")
        self.assertEqual(for_other.status_code, 400, for_other.content)
        line = riley.post("/api/hr/expense-lines/", {"claim": claim_id, "spent_on": "2026-06-01", "expense_account": self.travel.pk,
                                                     "description": "Train", "amount": "300"}, format="json")
        self.assertEqual(line.status_code, 201, line.content)
        self.assertEqual(riley.post(f"/api/hr/expense-claims/{claim_id}/submit/").status_code, 200)
        self.assertEqual(riley.post(f"/api/hr/expense-claims/{claim_id}/approve/").status_code, 403)
        # Sam sees nothing of Riley's; Jordan sees it and decides it.
        self.assertEqual(self.as_(self.other.user).get(f"/api/hr/expense-claims/{claim_id}/").status_code, 404)
        jordan = self.as_(self.manager.user)
        self.assertEqual([row["id"] for row in jordan.get("/api/hr/expense-claims/").json()], [claim_id])
        approved = jordan.post(f"/api/hr/expense-claims/{claim_id}/approve/", {"note": "ok"}, format="json")
        self.assertEqual((approved.status_code, approved.json()["status"], approved.json()["decided_by_name"]),
                         (200, "approved", "Jordan Park"), approved.content)
        self.assertEqual(jordan.post(f"/api/hr/expense-claims/{claim_id}/pay/", {"paid_from": self.cash.pk}, format="json").status_code, 403)
        books = self.as_(self.books)
        paid = books.post(f"/api/hr/expense-claims/{claim_id}/pay/", {"paid_from": self.cash.pk, "on_date": "2026-06-03"}, format="json")
        self.assertEqual((paid.status_code, paid.json()["status"], paid.json()["paid_from_name"], paid.json()["total"]),
                         (200, "paid", "Cash", "300.00"), paid.content)
        self.assertEqual(self.as_(self.hr).get("/api/hr/expense-claims/").json()[0]["id"], claim_id)


class AppraisalTests(PeopleTestCase):
    def test_written_submitted_and_acknowledged(self):
        with self.assertRaisesMessage(ValidationError, "Nobody appraises themselves"):
            Appraisal.objects.create(employee=self.riley, reviewer=self.riley, period_start=JUNE_1, period_end=datetime.date(2026, 6, 30))
        appraisal = Appraisal.objects.create(employee=self.riley, reviewer=self.manager, period_start=datetime.date(2026, 1, 1),
                                             period_end=JUNE_1, strengths="Steady on the loom")
        with self.assertRaisesMessage(ValidationError, "Rate it out of five"):
            appraisal.submit()
        appraisal.rating = 4
        appraisal.save()
        appraisal.submit()
        self.assertEqual(appraisal.status, AppraisalStatus.SUBMITTED)
        with self.assertRaisesMessage(ValidationError, "words stand once submitted"):
            appraisal.strengths = "edited"
            appraisal.save()
        appraisal.acknowledge("Noted; I will take the changeover training.")
        self.assertEqual((appraisal.status, appraisal.employee_comment[:5]), (AppraisalStatus.ACKNOWLEDGED, "Noted"))
        with self.assertRaisesMessage(ValidationError, "part of the record"):
            appraisal.delete()

    def test_the_reviewer_writes_it_and_the_person_reads_it_only_once_submitted(self):
        jordan = self.as_(self.manager.user)
        made = jordan.post("/api/hr/appraisals/", {"employee": self.riley.pk, "period_start": "2026-01-01", "period_end": "2026-06-01",
                                                   "rating": 4, "strengths": "Steady"}, format="json")
        self.assertEqual((made.status_code, made.json()["reviewer"], made.json()["status"]), (201, self.manager.pk, "draft"), made.content)
        riley = self.as_(self.riley.user)
        self.assertEqual(riley.get(f"/api/hr/appraisals/{made.json()['id']}/").status_code, 404)
        self.assertEqual(riley.post(f"/api/hr/appraisals/{made.json()['id']}/submit/").status_code, 403)
        self.assertEqual(jordan.post(f"/api/hr/appraisals/{made.json()['id']}/submit/").json()["status"], "submitted")
        seen = riley.get(f"/api/hr/appraisals/{made.json()['id']}/")
        self.assertEqual((seen.status_code, seen.json()["strengths"]), (200, "Steady"))
        self.assertEqual(jordan.post(f"/api/hr/appraisals/{made.json()['id']}/acknowledge/", {"comment": "x"}, format="json").status_code, 403)
        acknowledged = riley.post(f"/api/hr/appraisals/{made.json()['id']}/acknowledge/", {"comment": "Noted"}, format="json")
        self.assertEqual((acknowledged.status_code, acknowledged.json()["status"], acknowledged.json()["employee_comment"]),
                         (200, "acknowledged", "Noted"), acknowledged.content)
        self.assertEqual(self.as_(self.other.user).get(f"/api/hr/appraisals/{made.json()['id']}/").status_code, 404)


class RecruitmentTests(PeopleTestCase):
    def test_an_applicant_moves_forward_and_hired_is_on_the_rolls(self):
        opening = JobOpening.objects.create(title="Loom operator", department=self.weaving, openings=1, opened_on=JUNE_1)
        with self.assertRaisesMessage(ValidationError, "one person or more"):
            JobOpening.objects.create(title="Nobody", openings=0, opened_on=JUNE_1)
        asha = Applicant.objects.create(opening=opening, name="Asha Devi", phone="98765", applied_on=JUNE_1)
        bala = Applicant.objects.create(opening=opening, name="Bala", applied_on=JUNE_1)
        with self.assertRaisesMessage(ValidationError, "an offer is made and accepted first"):
            asha.hire("E101")
        with self.assertRaisesMessage(ValidationError, "hiring is its own step"):
            asha.advance(Stage.HIRED)
        asha.advance(Stage.INTERVIEW)
        with self.assertRaisesMessage(ValidationError, "applicants move forward"):
            asha.advance(Stage.SCREENING)
        asha.advance(Stage.OFFERED)
        with self.assertRaisesMessage(ValidationError, "Say why"):
            bala.reject("")
        bala.reject("Chose another plant")
        with self.assertRaisesMessage(ValidationError, "the record stands"):
            bala.notes = "x"
            bala.save()
        with self.assertRaisesMessage(ValidationError, "is taken"):
            asha.hire("E005")
        employee = asha.hire("E101", hire_date=datetime.date(2026, 7, 1))
        asha.refresh_from_db()
        opening.refresh_from_db()
        self.assertEqual((employee.employee_number, employee.party.name, employee.party.phone, employee.department, employee.job_title,
                          employee.hire_date), ("E101", "Asha Devi", "98765", self.weaving, "Loom operator", datetime.date(2026, 7, 1)))
        self.assertTrue(PartyRoleAssignment.objects.filter(party__code="E101", role=PartyRole.EMPLOYEE).exists())
        self.assertEqual((asha.stage, asha.employee, opening.status, opening.hired()), (Stage.HIRED, employee, OpeningStatus.FILLED, 1))
        with self.assertRaisesMessage(ValidationError, "has applicants; close it instead"):
            opening.delete()

    def test_the_personnel_office_runs_it_and_a_manager_reads(self):
        hr = self.as_(self.hr)
        opening = hr.post("/api/hr/job-openings/", {"title": "Loom operator", "department": self.weaving.pk, "openings": 1}, format="json")
        self.assertEqual((opening.status_code, opening.json()["status"], opening.json()["opened_on"]),
                         (201, "open", str(timezone.localdate())), opening.content)
        applicant = hr.post("/api/hr/applicants/", {"opening": opening.json()["id"], "name": "Asha Devi", "source": "referral"}, format="json")
        self.assertEqual(applicant.status_code, 201, applicant.content)
        pk = applicant.json()["id"]
        self.assertEqual(hr.post(f"/api/hr/applicants/{pk}/advance/", {"stage": "offered"}, format="json").json()["stage"], "offered")
        hired = hr.post(f"/api/hr/applicants/{pk}/hire/", {"employee_number": "E101", "hire_date": "2026-07-01"}, format="json")
        self.assertEqual((hired.status_code, hired.json()["stage"], hired.json()["employee_number"]), (200, "hired", "E101"), hired.content)
        self.assertEqual(Employee.objects.get(employee_number="E101").pk, hired.json()["employee"])
        self.assertEqual(hr.get(f"/api/hr/job-openings/{opening.json()['id']}/").json()["status"], "filled")
        jordan = self.as_(self.manager.user)
        self.assertEqual(jordan.get("/api/hr/applicants/").status_code, 200)
        self.assertEqual(jordan.post("/api/hr/applicants/", {"opening": opening.json()["id"], "name": "x"}, format="json").status_code, 403)
        self.assertEqual(self.as_(self.riley.user).get("/api/hr/job-openings/").status_code, 403)


class ClaimOnTheStatementTests(PeopleTestCase):
    """
    A claim of 300 + 150 = 450 paid from the bank on 7 July. July's statement:
    opening 0, one line -450, closing -450; the books say -450 too. The line is
    the claim's payment: matched to it, not posted again (calc_stat/o62_statement.py).
    """

    def setUp(self):
        super().setUp()
        from apps.accounting.models import BankStatement, BankStatementLine

        self.bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True)
        self.paid = self.claim()
        self.paid.submit()
        self.paid.approve(self.manager)
        self.paid.pay(self.bank, on_date=datetime.date(2026, 7, 7))
        self.july = BankStatement.objects.create(bank_account=self.bank, start_date=datetime.date(2026, 7, 1),
                                                 end_date=datetime.date(2026, 7, 31), opening_balance=Decimal("0"),
                                                 closing_balance=Decimal("-450"))
        self.line = BankStatementLine.objects.create(statement=self.july, date=datetime.date(2026, 7, 7),
                                                     amount=Decimal("-450"))

    def balance(self, account):
        return sum((row.debit - row.credit for row in JournalLine.objects.filter(account=account, entry__posted=True)),
                   Decimal("0"))

    def test_a_claim_paid_from_the_bank_is_matched_on_its_statement(self):
        self.line.match_entry(self.paid.journal_entry)
        report = self.july.reconciliation()
        self.assertEqual((report["difference"], report["unresolved_lines"], self.balance(self.bank),
                          self.balance(self.travel), self.balance(self.meals)),
                         (Decimal("0"), [], Decimal("-450"), Decimal("300"), Decimal("150")))
        self.july.close()

    def test_paid_in_june_and_on_the_bank_in_july_it_is_not_yet_presented_at_junes_end(self):
        from apps.accounting.models import BankStatement

        june = BankStatement.objects.create(bank_account=self.bank, start_date=datetime.date(2026, 6, 1),
                                            end_date=datetime.date(2026, 6, 30), opening_balance=Decimal("0"),
                                            closing_balance=Decimal("0"))
        late = self.claim()
        late.submit()
        late.approve(self.manager)
        late.pay(self.bank, on_date=datetime.date(2026, 6, 30))
        report = june.reconciliation()
        self.assertEqual((report["ledger_balance"], report["unpresented_total"], report["difference"]),
                         (Decimal("-450"), Decimal("-450"), Decimal("0")))

    def test_matched_by_the_bookkeeper_over_the_api(self):
        client = self.as_(self.books)
        response = client.post(f"/api/accounting/bank-statements/{self.july.pk}/match/",
                               {"line": self.line.pk, "entry": self.paid.journal_entry_id}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.line.refresh_from_db()
        self.assertEqual(self.line.booked_entry_id, self.paid.journal_entry_id)
        report = client.get(f"/api/accounting/bank-statements/{self.july.pk}/reconciliation/").json()
        self.assertEqual((report["difference"], report["unresolved_lines"]), ("0.00", 0))


class TheAuditSeesAnUnregisteredBankMovementTests(TestCase):
    """`audit_invariants` reports a document that pays from a bank by its own entry and is not registered."""

    def findings(self, edit=lambda path, text: text):
        from apps.core.management.commands.audit_invariants import Command, app_sources

        sources = app_sources()
        sources["hr"] = {path: edit(path, text) for path, text in sources["hr"].items()}
        return [detail.split(" ")[0] for _, detail in Command().bank_movements_unregistered(["hr"], sources)]

    def test_a_claim_paid_from_the_bank_unregistered_is_reported(self):
        self.assertEqual(self.findings(lambda path, text: text.replace("register_bank_movements(claims_paid)", "")
                                       if path.name == "apps.py" else text), ["hr.ExpenseClaim"])

    def test_as_it_stands_it_is_not(self):
        self.assertEqual(self.findings(), [])
