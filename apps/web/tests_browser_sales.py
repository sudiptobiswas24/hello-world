"""
An order from first line to money in the bank, worked in a browser by
the three people who do it here: the rep takes it, the store ships it,
accounts bill it and bank the cheque. Each step is read back from the
database, not just from the screen.

Then the refusals a screen must show rather than swallow.
"""

import re
from decimal import Decimal

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from django.utils import timezone

from apps.accounting.models import Payment
from apps.core.models import Company, Party, PartyRole
from apps.sales.models import Delivery, Invoice, SalesOrder

from .tests_browser import BrowserTestCase


class SalesInTheBrowserTests(BrowserTestCase):
    def setUp(self):
        super().setUp()
        company = Company.get()
        company.default_revenue_account = self.revenue
        company.default_receivable_account = self.ar
        company.default_bank_account = self.bank
        company.save()

    def url(self, path):
        return f"{self.live_server_url}/app{path}"

    def take_order(self, page, quantity):
        page.goto(self.url("/sales/orders/new"))
        page.get_by_role("combobox", name="Customer").fill("Acm")
        page.get_by_role("option", name=re.compile("Acme")).click()
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/sales/orders/\d+$"))
        page.get_by_role("combobox", name="Item to add").fill("WDG")
        page.get_by_role("option", name=re.compile("WDG-1")).click()
        page.get_by_label("Quantity to add").fill(quantity)
        page.get_by_role("button", name="Add line").click()
        expect(page.locator(".lines tbody tr", has_text="Widget")).to_have_count(1)
        page.get_by_role("button", name="Confirm", exact=True).click()
        expect(page.locator(".pill", has_text="confirmed")).to_be_visible()
        return SalesOrder.objects.get(pk=int(page.url.rsplit("/", 1)[1]))

    def test_an_order_from_first_line_to_money_in_the_bank(self):
        on_hand = self.item.on_hand_at(self.warehouse)

        # The rep takes the order. They may not ship it.
        rep = self.sign_in(self.person("Sales Rep"), "/app/sales/orders")
        order = self.take_order(rep, "10")
        self.assertEqual(order.status, "confirmed")
        line = order.lines.get()
        self.assertEqual((line.quantity, line.unit_price), (Decimal("10"), Decimal("10")))
        expect(rep.get_by_role("button", name="Ship", exact=True)).to_have_count(0)

        # The store ships it: one warehouse, so it is not asked which.
        store = self.new_page()
        self.sign_in(self.person("Warehouse Staff"), f"/app/sales/orders/{order.pk}", page=store)
        store.get_by_role("button", name="Ship", exact=True).click()
        store.wait_for_url(re.compile(r"/sales/deliveries/\d+$"))
        delivery = Delivery.objects.get(pk=int(store.url.rsplit("/", 1)[1]))
        self.assertFalse(delivery.posted)
        self.assertEqual(self.item.on_hand_at(self.warehouse), on_hand)  # a draft moves nothing
        store.get_by_role("button", name="Ship", exact=True).click()
        expect(store.locator(".pill", has_text="Shipped")).to_be_visible()
        delivery.refresh_from_db()
        self.assertTrue(delivery.posted)
        self.assertEqual(self.item.on_hand_at(self.warehouse), on_hand - Decimal("10"))
        expect(store.get_by_role("button", name="Invoice", exact=True)).to_have_count(0)

        # Accounts bill it, and bank the cheque against it.
        accounts = self.new_page()
        self.sign_in(self.person("AR Manager"), f"/app/sales/orders/{order.pk}", page=accounts)
        accounts.get_by_role("button", name="Invoice", exact=True).click()
        accounts.wait_for_url(re.compile(r"/sales/invoices/\d+$"))
        invoice = Invoice.objects.get(pk=int(accounts.url.rsplit("/", 1)[1]))
        accounts.get_by_role("button", name="Post", exact=True).click()
        self.toast(accounts, "posted")
        invoice.refresh_from_db()
        self.assertTrue(invoice.posted)
        self.assertEqual(invoice.amount_due(), Decimal("100.00"))
        self.assertEqual(self.balance(self.revenue), Decimal("-100.00"))

        accounts.get_by_role("link", name="Receive payment").click()
        accounts.get_by_label("Amount").fill("100")
        accounts.get_by_label("Reference").fill("CHQ 000123")
        accounts.get_by_role("button", name="Record and post").click()
        accounts.wait_for_url(re.compile(r"/sales/receipts/\d+\?invoice="))
        payment = Payment.objects.get(reference="CHQ 000123")
        self.assertTrue(payment.posted)
        self.assertEqual((payment.party, payment.amount), (self.customer, Decimal("100.00")))
        self.assertEqual(self.balance(self.bank), Decimal("100.00"))

        row = accounts.locator("tr.highlight")
        expect(row).to_contain_text(invoice.number)
        row.get_by_role("button", name="Apply").click()
        expect(accounts.get_by_text("All applied")).to_be_visible()
        invoice.refresh_from_db()
        self.assertEqual(invoice.amount_due(), Decimal("0.00"))
        self.assertEqual(self.balance(self.ar), Decimal("0.00"))
        self.assertEqual(self.problems, [])

    def test_a_shipment_the_shelf_cannot_cover_is_refused_in_words_and_stays_a_draft(self):
        rep = self.sign_in(self.person("Sales Rep"), "/app/sales/orders")
        order = self.take_order(rep, "600")  # 500 on the shelf
        store = self.new_page()
        self.sign_in(self.person("Warehouse Staff"), f"/app/sales/orders/{order.pk}", page=store)
        store.get_by_role("button", name="Ship", exact=True).click()
        store.wait_for_url(re.compile(r"/sales/deliveries/\d+$"))
        store.get_by_role("button", name="Ship", exact=True).click()
        expect(store.locator(".toast-bad")).to_be_visible()
        expect(store.locator(".pill", has_text="Draft")).to_be_visible()
        self.assertFalse(Delivery.objects.get(sales_order=order).posted)
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("500"))

    def test_with_two_warehouses_the_store_is_asked_which(self):
        from apps.inventory.models import Warehouse

        Warehouse.objects.create(code="WH2", name="Godown")
        Warehouse.objects.create(code="QC", name="Inspection", is_quarantine=True)
        rep = self.sign_in(self.person("Sales Rep"), "/app/sales/orders")
        order = self.take_order(rep, "5")
        store = self.new_page()
        self.sign_in(self.person("Warehouse Staff"), f"/app/sales/orders/{order.pk}", page=store)
        store.get_by_role("button", name="Ship", exact=True).click()
        choice = store.get_by_label("Ship from")
        # Quarantine never ships, so it is not offered.
        expect(choice.locator("option")).to_have_count(3)  # the prompt, WH1, WH2
        expect(store.get_by_role("button", name="Draft delivery")).to_be_disabled()
        choice.select_option(label="WH1 · Main")
        store.get_by_role("button", name="Draft delivery").click()
        store.wait_for_url(re.compile(r"/sales/deliveries/\d+$"))
        self.assertEqual(Delivery.objects.get(sales_order=order).lines.get().warehouse, self.warehouse)

    def test_a_second_ship_while_one_waits_is_refused_and_nothing_doubles(self):
        rep = self.sign_in(self.person("Sales Rep"), "/app/sales/orders")
        order = self.take_order(rep, "10")
        order.create_delivery(warehouse=self.warehouse)
        store = self.new_page()
        self.sign_in(self.person("Warehouse Staff"), f"/app/sales/orders/{order.pk}", page=store)
        store.get_by_role("button", name="Ship", exact=True).click()
        expect(store.locator(".toast-bad", has_text="already waiting")).to_be_visible()
        self.assertEqual(order.deliveries.count(), 1)

    def test_a_new_customer_is_one_a_rep_can_sell_to_at_once(self):
        rep = self.sign_in(self.person("Sales Rep"), "/app/sales/customers/new")
        rep.get_by_label("Code", exact=True).fill("C-77")
        rep.get_by_label("Name", exact=True).fill("Kisan Feeds")
        rep.get_by_role("button", name="Add customer").click()
        rep.wait_for_url(re.compile(r"/sales/customers/\d+$"))
        party = Party.objects.get(code="C-77")
        self.assertTrue(party.role_assignments.filter(role=PartyRole.CUSTOMER).exists())
        rep.goto(self.url("/sales/orders/new"))
        rep.get_by_role("combobox", name="Customer").fill("Kisan")
        expect(rep.get_by_role("option", name=re.compile("Kisan Feeds"))).to_be_visible()

    def test_accounts_opens_a_customer_in_one_form_and_holds_them(self):
        from django.contrib.auth.models import Group

        from apps.accounting.models import PartyTaxProfile
        from apps.core.models import Address, PartyRoleAssignment
        from apps.sales.models import CustomerProfile

        carrier = Party.objects.create(code="SRW", name="Sharma Roadways")
        PartyRoleAssignment.objects.create(party=carrier, role=PartyRole.VENDOR)
        person = self.person("AR Manager")
        person.groups.add(Group.objects.get(name="GST Officer"))  # who keeps the GSTIN as well
        page = self.sign_in(person, "/app/sales/customers/new")
        page.get_by_label("Code", exact=True).fill("KFL")
        page.get_by_label("Name", exact=True).fill("Konkan Fertilisers")
        page.get_by_label("CIN").fill("U24120MH2001PLC131234")
        billing = page.get_by_role("region", name="Address")
        billing.get_by_label("Line 1").fill("Nariman Point")
        billing.get_by_label("City").fill("Mumbai")
        page.get_by_label("Goods go to another address").check()
        shipping = page.get_by_role("region", name="Shipping address")
        shipping.get_by_label("Line 1").fill("MIDC Plot 7")
        shipping.get_by_label("City").fill("Ratnagiri")
        page.get_by_role("region", name="Whom to speak to").get_by_label("First name").fill("Anil")
        page.get_by_label("GSTIN").fill("27AABCD1234E1Z8")
        page.get_by_label("Freight").select_option(label="FOR destination: we deliver, the freight is ours")
        page.get_by_label("Usual transporter").fill("Sharma")
        page.get_by_role("option", name=re.compile("Sharma Roadways")).click()
        page.get_by_label("Sacks a bale").fill("500")
        page.get_by_role("button", name="Add customer").click()
        page.wait_for_url(re.compile(r"/sales/customers/\d+$"))
        made = Party.objects.get(code="KFL")
        self.assertEqual(sorted(Address.objects.filter(party=made).values_list("address_type", "city")),
                         [("billing", "Mumbai"), ("shipping", "Ratnagiri")])
        self.assertEqual(PartyTaxProfile.objects.get(party=made).gst_state, "27")
        terms = CustomerProfile.objects.get(party=made)
        self.assertEqual((terms.freight_terms, terms.transporter, terms.sacks_per_bale),
                         ("for_destination", carrier, 500))
        # On their page, the hold is asked for its reason before it is set.
        region = page.get_by_role("region", name="Sales terms")
        region.get_by_label("On credit hold").check()
        region.get_by_role("button", name="Save terms").click()
        expect(region.locator(".field.invalid .field-error")).to_contain_text("Say why they are on hold.")
        region.get_by_label("Why on hold").fill("March bill 45 days overdue")
        region.get_by_role("button", name="Save terms").click()
        expect(region.get_by_role("button", name="Save terms")).to_have_count(0)
        terms.refresh_from_db()
        self.assertEqual((terms.credit_hold, terms.credit_hold_reason), (True, "March bill 45 days overdue"))

    def test_a_refused_section_keeps_the_form_and_makes_nothing(self):
        from django.contrib.auth.models import Group

        person = self.person("AR Manager")
        person.groups.add(Group.objects.get(name="GST Officer"))
        page = self.sign_in(person, "/app/sales/customers/new")
        page.get_by_label("Code", exact=True).fill("KFL")
        page.get_by_label("Name", exact=True).fill("Konkan Fertilisers")
        page.get_by_label("GSTIN").fill("27AABCD1234E1Z9")  # its check character is wrong
        page.get_by_role("button", name="Add customer").click()
        expect(page.get_by_role("region", name="GST").locator(".form-error, .field-error")).to_be_visible()
        self.assertFalse(Party.objects.filter(code="KFL").exists())
        self.assertTrue(page.url.endswith("/sales/customers/new"))
        expect(page.get_by_label("Name", exact=True)).to_have_value("Konkan Fertilisers")

    def test_a_rep_notes_an_order_plans_a_call_and_closes_it_from_home(self):
        from apps.core.chatter import FollowUp, Note

        order = self.make_order(quantity="10", price="100")
        rep = self.sign_in(self.person("Sales Rep"), f"/app/sales/orders/{order.pk}")
        notes = rep.get_by_role("region", name="Notes")
        notes.get_by_label("A note").fill("Wants 50-kg sacks next time")
        notes.get_by_role("button", name="Add note").click()
        expect(notes.get_by_text("Wants 50-kg sacks next time")).to_be_visible()
        plans = rep.get_by_role("region", name="Follow-ups")
        plans.get_by_role("button", name="Plan a follow-up").click()
        plans.get_by_label("About").fill("Ask about the October schedule")
        plans.get_by_role("button", name="Plan it").click()
        expect(plans.get_by_text("Ask about the October schedule")).to_be_visible()
        planned = FollowUp.objects.get()
        self.assertEqual((planned.kind, planned.link), ("call", f"/sales/orders/{order.pk}"))
        # Due on the plant's day (not the browser's tomorrow less one, which the hour would decide):
        # it heads the rep's home page, and leads back to the order.
        FollowUp.objects.filter(pk=planned.pk).update(due_on=timezone.localdate())
        rep.goto(f"{self.live_server_url}/app/")
        mine = rep.get_by_role("table", name="Your follow-ups")
        expect(mine).to_contain_text("Today")
        mine.get_by_role("link", name="Ask about the October schedule").click()
        rep.wait_for_url(re.compile(rf"/sales/orders/{order.pk}$"))
        plans = rep.get_by_role("region", name="Follow-ups")
        plans.get_by_role("button", name="Done").click()
        plans.get_by_label("How it went").fill("They confirm 40,000 sacks")
        plans.get_by_role("button", name="Mark done").click()
        expect(rep.get_by_role("region", name="Notes")).to_contain_text("Call done: Ask about the October schedule. They confirm 40,000 sacks")
        self.assertEqual(Note.objects.count(), 2)
        self.assertEqual(self.problems, [])

    def test_the_ar_manager_gives_a_customer_to_a_rep_who_then_sees_only_theirs(self):
        from apps.core.models import PartyRoleAssignment
        from apps.sales.models import CustomerProfile

        rep = self.person("Sales Rep")  # carries the customers there are now: Acme
        made = []
        for code, name in (("C-80", "Beta Cement"), ("C-81", "Gamma Fertiliser")):
            party = Party.objects.create(code=code, name=name)
            PartyRoleAssignment.objects.create(party=party, role=PartyRole.CUSTOMER)
            made.append(party)
        beta, gamma = made
        ar = self.sign_in(self.person("AR Manager"), f"/app/sales/customers/{beta.pk}")
        terms = ar.get_by_role("region", name="Sales terms")
        terms.get_by_label("Sales rep").select_option(label=rep.employee.party.name)
        terms.get_by_role("button", name="Save").click()
        self.toast(ar, "Saved")
        self.assertEqual(CustomerProfile.objects.get(party=beta).sales_rep, rep.employee.party)

        page = self.sign_in(rep, "/app/sales/customers", page=self.new_page())
        expect(page.get_by_role("row", name=re.compile("Beta Cement"))).to_be_visible()
        expect(page.get_by_role("row", name=re.compile(self.customer.name))).to_be_visible()
        expect(page.get_by_text("Gamma Fertiliser")).to_have_count(0)
        # And what they cannot see is not found, not shown.
        page.goto(self.url(f"/sales/customers/{gamma.pk}"))
        expect(page.locator(".error-panel")).to_contain_text("Not found")
        page.wait_for_load_state("networkidle")
        self.problems.clear()  # that 404 was asked for
        # Their terms are read, not changed: a rep cannot hand a customer on.
        page.goto(self.url(f"/sales/customers/{beta.pk}"))
        expect(page.get_by_role("region", name="Sales terms")).to_contain_text(rep.employee.party.name)
        expect(page.get_by_role("region", name="Sales terms").get_by_role("combobox")).to_have_count(0)

    def test_a_credit_note_with_gst_on_an_invoice_the_old_system_issued(self):
        from apps.accounting.models import Account, AccountType, Tax
        from apps.sales.models import InvoiceLine

        def account(code, kind):
            return Account.objects.create(code=code, name=code, account_type=kind)

        gst = Tax.objects.create(code="GST18", name="GST 18%", rate=Decimal("18"),
                                 collected_account=account("2210", AccountType.LIABILITY),
                                 paid_account=account("1310", AccountType.ASSET))
        old = Invoice.objects.create(customer=self.customer, invoice_date="2026-09-10", reference="OLD/1",
                                     receivable_account=self.ar, is_opening_balance=True)
        InvoiceLine.objects.create(invoice=old, description="Opening balance: OLD/1", quantity=Decimal("1"),
                                   unit_price=Decimal("118000"), revenue_account=self.revenue)
        old.post()
        ar = self.sign_in(self.person("AR Manager"), f"/app/sales/invoices/{old.pk}")
        expect(ar.get_by_role("note")).to_contain_text("Brought in from the old system")
        ar.get_by_role("button", name="Credit note with GST").click()
        form = ar.get_by_role("form", name="Credit note with GST")
        form.get_by_label("Line 1 what").fill("Rate difference on OLD/1")
        form.get_by_label("Line 1 price").fill("10000")
        form.get_by_label("Line 1 tax").select_option(label="GST 18%")
        # Acme has no GSTIN: where the note is reported turns on what the
        # old invoice was for, and it is asked rather than guessed.
        expect(form.get_by_role("button", name="Post it")).to_be_disabled()
        form.get_by_label("What the old invoice was for in all").fill("118000")
        form.get_by_role("button", name="Post it").click()
        self.toast(ar, "posted")
        note = Invoice.objects.get(credits=old)
        ar.wait_for_url(re.compile(rf"/sales/invoices/{note.pk}$"))
        self.assertEqual((note.total(), note.corrects_old_supply, note.posted), (Decimal("11800.00"), True, True))
        expect(ar.locator(".doc-head")).to_contain_text(note.number)
        old.refresh_from_db()
        self.assertEqual(old.amount_due(), Decimal("106200.00"))
        self.assertEqual(self.problems, [])

    def test_a_duplicate_code_is_said_beside_the_field(self):
        rep = self.sign_in(self.person("Sales Rep"), "/app/sales/customers/new")
        rep.get_by_label("Code", exact=True).fill("C-1")  # Acme's
        rep.get_by_label("Name", exact=True).fill("Someone else")
        rep.get_by_role("button", name="Add customer").click()
        expect(rep.locator(".field.invalid .field-error")).to_contain_text("code")
        self.assertEqual(Party.objects.filter(code="C-1").count(), 1)
        self.assertTrue(rep.url.endswith("/sales/customers/new"))

    def test_every_role_opens_what_it_reads_without_being_refused(self):
        """A screen asks only for what the person may read: a related list or
        panel the server refuses is a fault even when the page looks fine."""
        from django.contrib.auth.models import Permission

        order = self.make_order(quantity="10", price="100")
        delivery = self.ship(order, "10")
        invoice = self.bill(order)
        payment = self.receipt("100")
        self.allocate(payment, invoice, "100")
        screens = [
            ("sales.view_salesorder", f"/sales/orders/{order.pk}", order.number),
            ("sales.view_delivery", f"/sales/deliveries/{delivery.pk}", delivery.number),
            ("sales.view_invoice", f"/sales/invoices/{invoice.pk}", invoice.number),
            ("accounting.view_payment", f"/sales/receipts/{payment.pk}", payment.number),
            ("core.view_party", f"/sales/customers/{self.customer.pk}", self.customer.name),
        ]
        for role in ["Sales Rep", "Warehouse Staff", "AR Manager", "AP Manager"]:
            with self.subTest(role=role):
                person = self.person(role)
                held = {f"{app}.{code}" for app, code in Permission.objects.filter(
                    group__user=person).values_list("content_type__app_label", "codename")}
                page = self.new_page()
                self.sign_in(person, "/app/", page=page)
                opened = 0
                for permission, path, heading in screens:
                    if permission not in held:
                        continue
                    page.goto(self.url(path))
                    expect(page.locator(".doc-head, .doc").first).to_contain_text(heading)
                    page.wait_for_load_state("networkidle")
                    opened += 1
                self.assertGreater(opened, 0)
                self.assertEqual(self.problems, [], role)


    def test_the_rep_closes_the_rest_of_a_line_short_and_reopens_it(self):
        order = self.make_order(quantity="10", price="100")
        self.ship(order, "6")
        line = order.lines.get()
        rep = self.sign_in(self.person("Sales Rep"), f"/app/sales/orders/{order.pk}")
        row = rep.locator(".lines tbody tr", has_text="Widget")
        row.get_by_role("button", name=re.compile(r"^Close .*Widget short$")).click()
        row.get_by_label(re.compile(r"^Why the rest of .*Widget will not ship$")).fill("Customer cut the order")
        row.get_by_role("button", name="Close", exact=True).click()
        expect(row.get_by_text("Closed short")).to_be_visible()
        line.refresh_from_db()
        self.assertEqual((line.closed_short_reason, line.quantity_open()), ("Customer cut the order", Decimal("0")))
        row.get_by_role("button", name="Reopen").click()
        expect(row.get_by_role("button", name=re.compile("short$"))).to_be_visible()
        line.refresh_from_db()
        self.assertEqual(line.quantity_open(), Decimal("4"))
        self.assertEqual(self.problems, [])

    def test_part_of_a_deposit_is_given_back_from_the_screen(self):
        order = self.make_order(quantity="10", price="100")
        deposit = order.create_down_payment_invoice(self.ar, percent=30)
        deposit.post()
        ar = self.sign_in(self.person("AR Manager"), f"/app/sales/invoices/{deposit.pk}")
        with self.answering(ar, "90") as asked:
            ar.get_by_role("button", name="Credit note").click()
            ar.wait_for_url(re.compile(r"/sales/invoices/(?!%d$)\d+$" % deposit.pk))
        self.assertIn("Leave it empty for all that is left", asked[0])
        deposit.refresh_from_db()
        self.assertEqual(deposit.deposit_unapplied(), Decimal("210.00"))
        note = Invoice.objects.get(credits=deposit)
        self.assertEqual(note.total(), Decimal("90.00"))
        expect(ar.locator(".doc-head")).to_contain_text(note.number)
        self.assertEqual(self.problems, [])
