

class TheBooksAndTheFloorsMastersShowTheDemoTests(SignsInAsTheDemo, TestCase):
    """
    O613 (the last of it): bank statements, salary files, remittances and the floor's masters answered nothing on the
    demo. HDFC's statement for last month carries what the month moved through the account and the bank's charges,
    matched as the office matches it and left open; the posted run's salary file is paid and its provident fund paid
    over to the EPFO; the press has its setup families and changeovers, the looms and the extruder their critical
    positions, the day shift its crew, the plant its power tariff and its lane to the godown; the runs on the floor
    name the tools they wear. Read by the accountant, the payroll officer, the production supervisor and maintenance.
    """

    @classmethod
    def setUpTestData(cls):
        load(password="Trial-2026", stdout=StringIO(), stderr=StringIO())

    def read(self, path, username):
        answer = self.as_(username).get(path)
        self.assertEqual(answer.status_code, 200, (path, answer.content[:300]))
        return json.loads(answer.content)

    def test_each_months_statement_follows_the_last_and_differs_from_the_books_by_the_charges_alone(self):
        statements = sorted(self.read("/api/accounting/bank-statements/", "anita"), key=lambda row: row["start_date"])
        self.assertEqual(len(statements), 3)
        for before, after in zip(statements, statements[1:]):
            self.assertEqual(after["opening_balance"], before["closing_balance"])
        self.assertEqual({row["closed"] for row in statements}, {False})
        last = statements[-1]
        lines = self.read(f"/api/accounting/bank-statement-lines/?statement={last['id']}", "anita")
        self.assertEqual(Decimal(last["closing_balance"]) - Decimal(last["opening_balance"]),
                         sum(Decimal(line["amount"]) for line in lines))
        charges = [line for line in lines if line["description"].startswith("Charges")]
        self.assertEqual([(line["amount"], line["payment"], line["booked_entry"]) for line in charges],
                         [("-590.00", None, None)])
        self.assertTrue(any(line["payment"] for line in lines), lines)
        # What the bank took that the books have not booked is the whole difference: every payment of the months
        # the statements cover was presented on one of them.
        report = self.read(f"/api/accounting/bank-statements/{last['id']}/reconciliation/", "anita")
        self.assertEqual((report["unpresented"], report["difference"], report["reconciled"]), ([], "590.00", False))

    def test_the_posted_run_is_paid_by_its_salary_file_and_its_provident_fund_paid_over(self):
        from apps.hr.payroll import PayRun, _deducted, remitted

        (paid,) = self.read("/api/hr/salary-files/", "kiran")
        self.assertIsNotNone(paid["payment"])
        run = PayRun.objects.get(status="posted")
        remittances = self.read("/api/hr/statutory-remittances/", "kiran")
        self.assertTrue(remittances)
        for remittance in remittances:
            from apps.accounting.models import Account

            account = Account.objects.get(pk=remittance["liability_account"])
            self.assertEqual(remitted(account, run.period_start), _deducted(account, run.period_start), account)

    def test_the_floor_has_its_families_changeovers_positions_crew_tariff_and_tools(self):
        def read(path):
            return self.read(f"/api/manufacturing/{path}/", "suresh")

        self.assertEqual(sorted({row["family"] for row in read("setup-families")}),
                         ["2C-CEMENT", "2C-FOOD", "4C-FOOD"])
        self.assertEqual(len(read("changeover-rules")), 4)
        self.assertEqual(sorted(row["code"] for row in read("machine-positions")),
                         ["BRG-SHUTTLE", "BRG-SHUTTLE", "HEATER-Z3"])
        self.assertEqual(len(read("crew-assignments")), 3)
        self.assertEqual([row["rate"] for row in self.read("/api/manufacturing/energy-tariffs/", "vikas")],
                         ["8.6500"])
        self.assertEqual([row["code"] for row in read("print-designs")], ["D-ATTA25"])
        self.assertEqual(len(read("tape-run-settings")), 1)
        from apps.manufacturing.tooling import Tool, ToolUsage

        self.assertEqual(sorted(row["code"] for row in read("tools")),
                         ["CYL-ATTA-1", "CYL-ATTA-2", "DIE-5080", "REED-50"])
        worn = set(ToolUsage.objects.values_list("tool__code", flat=True))
        self.assertEqual(worn, set(Tool.objects.values_list("code", flat=True)))

    def test_the_lane_to_the_godown_the_tagged_customers_and_two_suppliers_banks(self):
        (lane,) = self.read("/api/planning/transfer-routes/", "suresh")
        self.assertEqual(lane["lead_days"], 1)
        self.assertEqual(sorted(tag["name"] for tag in self.read("/api/core/party-tags/", "anita")),
                         ["Export", "Key account"])
        self.assertEqual(sorted((row["bank_name"].split(",")[0], row["ifsc"], row["is_primary"])
                                for row in self.read("/api/core/bank-accounts/", "ravi")),
                         [("ICICI Bank", "ICIC0000004", True), ("State Bank of India", "SBIN0000575", True)])
