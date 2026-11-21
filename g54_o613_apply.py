"""O613's last part: applied to g54's load_demo.py."""
import sys
p = sys.argv[1] + "/apps/web/management/commands/load_demo.py"
s = open(p).read()

def put(old, new):
    global s
    assert s.count(old) == 1, old[:80]
    s = s.replace(old, new)

# 0. The opening entry, kept: the bank's first statement opens at it rather than listing it.
put('''                    Line.objects.create(entry=entry, account=account, debit=D(debit), credit=D(credit), cost_centre=centre)
                entry.post()
''', '''                    Line.objects.create(entry=entry, account=account, debit=D(debit), credit=D(credit), cost_centre=centre)
                entry.post()
                return entry
''')
put('''            journal(on(2026, 7, 1), "Opening bank balance",''', '''            go_live = journal(on(2026, 7, 1), "Opening bank balance",''')

# 1. The floor: tools the runs name wear as they book (ProductionEntry._wear_tools), and the tape line's settings.
put('''            weave = start_run(fabric_50, f50.bom, "600", looms, day(-2), 20)
            on_its_machine(weave, looms, loom)
''', '''            weave = start_run(fabric_50, f50.bom, "600", looms, day(-2), 20)
            on_its_machine(weave, looms, loom)
            # O613: the tools a run names wear as it books (ProductionEntry._wear_tools): the loom's reed here, the
            # atta design's cylinders and the cutting die on the sacks below. And what the tape line ran at.
            Tool = M("manufacturing.Tool")
            weave.tools.add(Tool.objects.create(code="REED-50", name="Reed, 50 cm tube", kind="reed", work_centre=looms,
                                                life_limit=D("150000"), life_uom=kg, acquired_on=on(2026, 4, 20)))
            M("manufacturing.TapeRunSetting").objects.create(
                work_order=spec_run, machine=machines["EXT-1"], recorded_at=stamp(day(-3), 7), draw_ratio=D("6.20"),
                quench_temperature_c=D("32.0"), oven_temperature_c=D("145.0"), note="As the T-1000 trial settled")
''')
put('''            sacks = start_run(atta, atta_spec.bom, "2000", conv, yesterday, 8)
            on_its_machine(sacks, conv, stitcher)
''', '''            sacks = start_run(atta, atta_spec.bom, "2000", conv, yesterday, 8)
            on_its_machine(sacks, conv, stitcher)
            atta_design = M("manufacturing.PrintDesign").objects.create(
                code="D-ATTA25", name="Atta 25 kg, wheat sheaf, 2-colour", customer=konkan, colours=2,
                artwork_reference="KAF/ART/2026-07", approved_on=day(-40), approved_by=suresh.party)
            sacks.tools.add(*[Tool.objects.create(code=f"CYL-ATTA-{n}", name=f"Atta cylinder, colour {n}",
                                                  kind="cylinder", design=atta_design, work_centre=press,
                                                  life_limit=D("3000000"), life_uom=nos, acquired_on=day(-35))
                              for n in (1, 2)],
                            Tool.objects.create(code="DIE-5080", name="Cutting die, 50 x 80 cm", kind="die",
                                                work_centre=conv, life_limit=D("800000"), life_uom=nos,
                                                acquired_on=on(2026, 5, 2)))
''')

# 2. Masters the floor and the planner read, in a step of their own after the floor's.
put('''        with step("Stoppages"):
''', '''        with step("The floor's masters: setup families, changeovers, positions, crews, the tariff, a lane, tags"):
            # O613: what the schedule, maintenance and energy screens read had nothing on the demo. The press's
            # families and the minutes between them, the looms' and the extruder's critical positions with their
            # spares, who works where on the day shift, the power tariff, the lane from the plant to the godown,
            # the customers the office tags, and where two suppliers are paid.
            for family_item, family in ((cement, "2C-CEMENT"), (fert, "2C-CEMENT"), (rice, "4C-FOOD"), (atta, "2C-FOOD")):
                M("manufacturing.SetupFamily").objects.create(item=family_item, work_centre=press, family=family)
            for start_family, end_family, minutes, purge in (
                    ("2C-CEMENT", "4C-FOOD", "90", "6"), ("4C-FOOD", "2C-CEMENT", "60", "4"),
                    ("2C-FOOD", "4C-FOOD", "45", "3"), ("", "", "20", "1")):
                M("manufacturing.ChangeoverRule").objects.create(work_centre=press, from_family=start_family,
                                                                 to_family=end_family, minutes=D(minutes),
                                                                 purge_kg=D(purge))
            for machine_code, code, name, spare in (("LOOM-05", "BRG-SHUTTLE", "Shuttle wheel bearing", bearing),
                                                    ("LOOM-03", "BRG-SHUTTLE", "Shuttle wheel bearing", bearing),
                                                    ("EXT-1", "HEATER-Z3", "Barrel heater, zone 3", heater)):
                M("manufacturing.MachinePosition").objects.create(machine=machines[machine_code], code=code, name=name,
                                                                  spare_item=spare, is_critical=True)
            for employee, centre in ((mahesh, ext), (lakshmi, looms), (ganesh, conv)):
                M("manufacturing.CrewAssignment").objects.create(employee=employee, work_centre=centre,
                                                                 shift=shifts[0], valid_from=day(-30))
            M("manufacturing.EnergyTariff").objects.create(valid_from=on(2026, 4, 1), rate=D("8.6500"),
                                                           note="HT industrial, with the fixed share")
            M("planning.TransferRoute").objects.create(from_warehouse=plant, to_warehouse=godown, lead_days=1,
                                                       notes="Across the yard, the evening forklift run")
            key_account, exporter = (M("core.PartyTag").objects.create(name=name, description=why) for name, why in (
                ("Key account", "Over a crore a year; the owner calls them himself"),
                ("Export", "Ships under the LUT; documents in dollars")))
            sahyadri.tags.add(key_account)
            godavari.tags.add(key_account)
            gulf.tags.add(exporter)
            for vendor, bank_name, number, ifsc in (
                    (granule, "ICICI Bank, Vadodara Alkapuri", "000405012345", "ICIC0000004"),
                    (western, "State Bank of India, Thane West", "38291045672", "SBIN0000575")):
                M("core.PartyBankAccount").objects.create(party=vendor, account_name=vendor.name, bank_name=bank_name,
                                                          account_number=number, ifsc=ifsc, currency=inr, is_primary=True)

        with step("Stoppages"):
''')

# 3. The books, last: the posted run's salary file paid and its PF paid over; then last month's bank statement,
#    which mirrors everything the month moved through HDFC, so it is written after everything else.
s = s.rstrip("\n") + '''

        with step("Payroll paid: the posted run's salary file written, and it and the provident fund paid"):
            # O613: salary files and remittances answered nothing on the demo. The posted run's bank file is written
            # and paid from HDFC; the month's provident fund, the people's share and the plant's, goes to the EPFO.
            # Three steps, as the office does it in three requests: posting a payment holds its period, and marking
            # the file paid then holds the slips, which the lock order ranks before the period.
            lock_rows(*M("hr.Employee").objects.order_by("pk"), refresh=False)
            from apps.hr.bank_file import SalaryTransferFile
            from apps.hr.payroll import _deducted, _net_pay_account, remitted

            Payment = M("accounting.Payment")
            posted_run = M("hr.PayRun").objects.get(status="posted")
            transfers = SalaryTransferFile.prepare(posted_run)
            hdfc = party("V-HDFC", "HDFC Bank, Chakan branch", "vendor", "Pune", "410501", None, net15,
                         ("Branch", "Manager", "Salary accounts"), "chakan@hdfcbank.example", state=STATES["27"])
            salaries_paid = Payment.objects.create(
                party=hdfc, direction="disbursement", amount=transfers.total, payment_date=posted_run.pay_date,
                currency=inr, bank_account=bank, counterpart_account=_net_pay_account(),
                reference=f"Salary file {posted_run.name}")
            salaries_paid.post()
            epfo = party("V-EPFO", "Employees' Provident Fund Organisation", "vendor", "Pune", "411001", None, net15,
                         ("Regional", "Office", "Pune"), "ro.pune@epfindia.example", state=STATES["27"])
            challans = []
            # One challan an account: the people's share and the plant's are both owed into PF payable here.
            for liability in sorted({pay_components[code].liability_account for code in ("PF", "PF-ER")},
                                    key=lambda account: account.code):
                owed = _deducted(liability, posted_run.period_start) - remitted(liability, posted_run.period_start)
                if owed <= 0:
                    raise CommandError(f"The posted run owes nothing into {liability}.")
                challan = Payment.objects.create(
                    party=epfo, direction="disbursement", amount=owed, currency=inr, bank_account=bank,
                    payment_date=posted_run.pay_date + datetime.timedelta(days=12), counterpart_account=liability,
                    reference=f"ECR {posted_run.name}, {liability.code}")
                challan.post()
                challans.append(challan)

        with step("Payroll paid: the provident fund remitted by its challans"):
            # Each remittance holds its payment, then its account (locked_before_it): all of them held first, in
            # the lock order, so the second remittance takes no payment after the first one's account.
            lock_rows(*challans, *(challan.counterpart_account for challan in challans))
            for challan in challans:
                M("hr.StatutoryRemittance").objects.create(payment=challan, liability_account=challan.counterpart_account,
                                                           period=posted_run.period_start, amount=challan.amount)

        with step("Payroll paid: the salary file marked paid by its payment"):
            transfers.mark_paid(salaries_paid)

        with step("The bank's statements from going live to last month, matched to the books"):
            # O613: bank statements answered nothing on the demo. HDFC's statement for each month from going live:
            # the first opens at the balance the plant went live with, and each carries every entry the month moved
            # through the account. Last month's also has the bank's charges the books do not have yet, left for the
            # accountant to book. Matched as the office matches them. Every month stays open: the power bill, the
            # rent and the bank's own charges were journals typed through the bank, which a statement line cannot
            # yet be explained by (O614).
            from apps.accounting.models import moved_through

            month_start, last_start = go_live.date.replace(day=1), s.month_span(2026, 10, -1)[0]
            balance = moved_through(go_live, bank)
            matched = []
            while month_start <= last_start:
                month_end = month_start.replace(day=calendar.monthrange(month_start.year, month_start.month)[1])
                through = M("accounting.JournalEntry").objects.filter(
                    posted=True, lines__account=bank, date__range=(month_start, month_end)).exclude(
                    pk=go_live.pk).distinct().order_by("date", "pk")
                statement_rows = [(entry.date, (entry.reference or entry.memo)[:255], moved_through(entry, bank))
                                  for entry in through]
                statement_rows = [row for row in statement_rows if row[2]]
                if month_start == last_start:
                    statement_rows.append((month_end, "Charges incl GST: NEFT, statement", D("-590.00")))
                closing = balance + sum((row[2] for row in statement_rows), D("0"))
                statement = M("accounting.BankStatement").objects.create(
                    bank_account=bank, reference=f"HDFC {month_start:%b %Y}", start_date=month_start,
                    end_date=month_end, opening_balance=balance, closing_balance=closing)
                for when, words, amount in statement_rows:
                    M("accounting.BankStatementLine").objects.create(statement=statement, date=when,
                                                                     description=words, amount=amount)
                matched += statement.auto_match()
                balance, month_start = closing, month_end + datetime.timedelta(days=1)
            if not matched:
                raise CommandError("Nothing on HDFC's statements matched the books.")
'''
open(p, "w").write(s)
print("applied")
