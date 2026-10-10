"""Throwaway MigrationExecutor tests for purchasing 0063, 0064 and hr 0021 (review_stat2)."""
import datetime

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, tag

P62 = [("purchasing", "0062_debit_note_keeps_the_suppliers_credit_note")]
P63 = [("purchasing", "0063_a_supplier_credit_note_is_one_in_its_year")]
P63_PREV = P62
H20 = [("hr", "0020_payslip_hours_handed_in")]
H21 = [("hr", "0021_a_claim_keeps_every_entry_it_posted")]
D = datetime.date


def show(*a):
    print("RV2", *a)


def back_to(target):
    executor = MigrationExecutor(connection)
    executor.migrate(target)
    return executor, executor.loader.project_state(target).apps


def forward(target):
    executor = MigrationExecutor(connection)
    executor.migrate(target)
    return executor.loader.project_state(target).apps


def leaf():
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


@tag("migration")
class Purchasing0063(TransactionTestCase):
    def setUp(self):
        self.addCleanup(leaf)
        self.executor, self.old = back_to(P62)
        now = self.executor.loader.project_state(self.executor.loader.graph.leaf_nodes()).apps
        Party = now.get_model("core", "Party")
        Account = now.get_model("accounting", "Account")
        self.v1 = Party.objects.create(code="V1", name="V1")
        self.v2 = Party.objects.create(code="V2", name="V2")
        self.pay = Account.objects.create(code="2000", name="AP", account_type="liability")

    def bill(self, vendor, day, number="", note_date=None, debits=None, n=[0]):
        Bill = self.old.get_model("purchasing", "Bill")
        n[0] += 1
        return Bill.objects.create(vendor_id=vendor.pk, bill_date=day, payable_account_id=self.pay.pk,
                                   number=f"B{n[0]}", debits_id=debits.pk if debits else None,
                                   supplier_note_number=number, supplier_note_date=note_date)

    def test_keys_each_note_in_its_year(self):
        orig1 = self.bill(self.v1, D(2026, 5, 1))
        orig2 = self.bill(self.v2, D(2026, 5, 1))
        rows = {
            "dated_fy26": self.bill(self.v1, D(2026, 6, 20), "CN-9", D(2026, 6, 10), orig1),
            "same_no_fy27": self.bill(self.v1, D(2027, 5, 2), "CN 9", D(2027, 5, 1), orig1),
            "undated_feb": self.bill(self.v1, D(2027, 2, 10), "CN/007", None, orig1),
            "nonum": self.bill(self.v1, D(2026, 6, 21), "", None, orig1),
            "other_vendor": self.bill(self.v2, D(2026, 6, 20), "CN-9", D(2026, 6, 10), orig2),
            "31mar": self.bill(self.v1, D(2027, 4, 1), "X 5", D(2027, 3, 31), orig1),
            "1apr": self.bill(self.v1, D(2027, 4, 1), "X 6", D(2027, 4, 1), orig1),
        }
        new = forward(P63)
        Bill = new.get_model("purchasing", "Bill")
        got = {k: Bill.objects.get(pk=v.pk).supplier_note_key for k, v in rows.items()}
        got["orig1"] = Bill.objects.get(pk=orig1.pk).supplier_note_key
        show("0063 keys", got)
        self.assertEqual(got["dated_fy26"], "2026:CN9")
        self.assertEqual(got["same_no_fy27"], "2027:CN9")
        self.assertEqual(got["undated_feb"], "2026:CN7")
        self.assertEqual(got["nonum"], "")
        self.assertEqual(got["other_vendor"], "2026:CN9")
        self.assertEqual(got["31mar"], "2026:X5")
        self.assertEqual(got["1apr"], "2027:X6")
        self.assertEqual(got["orig1"], "")

    def test_old_rows_that_differ_only_in_case_or_separators(self):
        """The old constraint was exact, so 'CN-9' and 'cn-9' could both stand. Forward must not crash."""
        orig = self.bill(self.v1, D(2026, 5, 1))
        self.bill(self.v1, D(2026, 6, 20), "CN-9", D(2026, 6, 10), orig)
        self.bill(self.v1, D(2026, 6, 21), "cn-9", D(2026, 6, 11), orig)
        try:
            forward(P63)
        except Exception as e:  # noqa
            show("0063 duplicate-after-normalising: FAILED forward:", type(e).__name__, e)
            raise
        show("0063 duplicate-after-normalising: ok")


@tag("migration")
class Purchasing0064(TransactionTestCase):
    def test_receipts_keep_going(self):
        self.addCleanup(leaf)
        executor, old = back_to(P63)
        now = executor.loader.project_state(executor.loader.graph.leaf_nodes()).apps
        Party = now.get_model("core", "Party")
        v = Party.objects.create(code="VV", name="VV")
        PO = old.get_model("purchasing", "PurchaseOrder")
        GR = old.get_model("purchasing", "GoodsReceipt")
        po = PO.objects.create(vendor_id=v.pk, order_date=D(2026, 6, 1))
        r1 = GR.objects.create(purchase_order=po, receipt_date=D(2026, 6, 10), posted=True)
        r2 = GR.objects.create(purchase_order=po, receipt_date=D(2026, 6, 12), posted=False)
        new = forward([("purchasing", "0064_an_objection_in_writing")])
        GR = new.get_model("purchasing", "GoodsReceipt")
        for r in (r1, r2):
            row = GR.objects.get(pk=r.pk)
            show("0064", row.pk, row.posted, row.objected_on, repr(row.objection), row.objection_removed_on)
            self.assertIsNone(row.objected_on)
            self.assertEqual(row.objection, "")
            self.assertIsNone(row.objection_removed_on)
        # And the rule on the receipt as it now stands: delivery date is the acceptance day.
        leaf()
        from apps.purchasing.models import GoodsReceipt
        from apps.purchasing.msme import acceptance
        self.assertEqual(acceptance(GoodsReceipt.objects.get(pk=r1.pk)), D(2026, 6, 10))


@tag("migration")
class Hr0021(TransactionTestCase):
    def test_claims_keep_what_they_still_name(self):
        self.addCleanup(leaf)
        executor, old = back_to(H20)
        now = executor.loader.project_state(executor.loader.graph.leaf_nodes()).apps
        Party = now.get_model("core", "Party")
        JE = now.get_model("accounting", "JournalEntry")
        person = Party.objects.create(code="EMP", name="Emp")
        Emp = old.get_model("hr", "Employee")
        emp = Emp.objects.create(party_id=person.pk, employee_number="E1", hire_date=D(2020, 1, 1))
        Claim = old.get_model("hr", "ExpenseClaim")
        n = [0]

        def je(reverses=None):
            n[0] += 1
            return JE.objects.create(date=D(2026, 6, 1), memo=f"e{n[0]}", reverses_id=reverses.pk if reverses else None)

        def claim(status="paid", entry=None, voided=None):
            n[0] += 1
            return Claim.objects.create(employee=emp, claim_date=D(2026, 6, 1), purpose=f"c{n[0]}", status=status,
                                        journal_entry_id=entry.pk if entry else None,
                                        voided_entry_id=voided.pk if voided else None)
        e1 = je(); c1 = claim("paid", e1)                                   # paid once
        e2 = je(); r2 = je(e2); c2 = claim("approved", e2, r2)              # paid, unpaid
        e3a = je(); r3a = je(e3a); e3b = je(); c3 = claim("paid", e3b, r3a)  # paid, unpaid, paid
        e5a = je(); r5a = je(e5a); e5b = je(); r5b = je(e5b); c5 = claim("approved", e5b, r5b)  # twice (first pair lost)
        c4 = claim("approved")
        c6 = claim("draft")
        new = forward(H21)
        Claim = new.get_model("hr", "ExpenseClaim")
        got = {name: sorted(Claim.objects.get(pk=c.pk).bank_entries.values_list("pk", flat=True))
               for name, c in [("c1", c1), ("c2", c2), ("c3", c3), ("c4", c4), ("c5", c5), ("c6", c6)]}
        show("hr0021", got, {"e1": e1.pk, "e2": e2.pk, "r2": r2.pk, "e3a": e3a.pk, "r3a": r3a.pk, "e3b": e3b.pk,
                              "e5a": e5a.pk, "r5a": r5a.pk, "e5b": e5b.pk, "r5b": r5b.pk})
        self.assertEqual(got["c1"], [e1.pk])
        self.assertEqual(got["c2"], sorted([e2.pk, r2.pk]))
        self.assertEqual(got["c3"], sorted([e3a.pk, r3a.pk, e3b.pk]))
        self.assertEqual(got["c4"], [])
        self.assertEqual(got["c6"], [])
        self.assertEqual(got["c5"], sorted([e5b.pk, r5b.pk]))
        # the reader after the migration
        leaf()
        from apps.hr.expenses import claims_paid
        seen = set(claims_paid().values_list("pk", flat=True))
        self.assertEqual(seen, {e1.pk, e2.pk, r2.pk, e3a.pk, r3a.pk, e3b.pk, e5b.pk, r5b.pk})
