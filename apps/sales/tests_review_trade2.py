"""review_trade2 probes (disposable)."""
import datetime
from django.utils import timezone as _tz
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounting.models import Account, AccountType
from apps.core.tests_roles import RolesTestCase

from .models import (ApprovalPolicy, DunningLevel, DunningNotice, Invoice, InvoiceLine, SalesOrder,
                     SalesOrderLine, run_dunning)
from .tests_base import SalesTestCase, carries_every_customer


class Probe(RolesTestCase, SalesTestCase):
    def setUp(self):
        SalesTestCase.setUp(self)
        RolesTestCase.setUp(self)

    def client_as(self, role):
        user = get_user_model().objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        carries_every_customer(user)
        c = APIClient()
        c.force_authenticate(user)
        return c

    def inv(self, qty="10", price="100"):
        order = self.make_order(quantity=qty, price=price)
        d = self.ship(order, int(qty))
        return d, self.bill(order)

    def typed_note(self, invoice, amount, **kw):
        note = Invoice.objects.create(customer=invoice.customer, invoice_date=kw.pop("invoice_date", invoice.invoice_date),
                                      receivable_account=invoice.receivable_account, currency=invoice.currency,
                                      credits=invoice)
        InvoiceLine.objects.create(invoice=note, description="goodwill", quantity=Decimal("1"),
                                   unit_price=Decimal(amount), revenue_account=self.revenue)
        return note

    # ---- O336: roles via API
    def test_s1_draft_then_ar_then_post_via_api(self):
        d, invoice = self.inv()
        line = invoice.lines.get()
        d.create_return(quantities={d.lines.get(): Decimal("4")}, post_notes=False)
        ar = self.client_as("AR Manager")
        r = ar.post(f"/api/sales/invoices/{invoice.pk}/credit_note/", {"quantities": {str(line.pk): "7"}}, format="json")
        print("S1 7 ->", r.status_code, r.content[:200])
        r = ar.post(f"/api/sales/invoices/{invoice.pk}/credit_note/", {"quantities": {str(line.pk): "6"}}, format="json")
        print("S1 6 ->", r.status_code)
        r = ar.post(f"/api/sales/invoices/{invoice.pk}/credit_note/", {}, format="json")
        print("S1 whole after ->", r.status_code, r.content[:200])
        draft = Invoice.objects.get(credits=invoice, posted=False)
        r = ar.post(f"/api/sales/invoices/{draft.pk}/post_invoice/", {}, format="json")
        print("S1 post draft ->", r.status_code, r.content[:200])
        print("S1 due", invoice.amount_due())

    def test_s1b_two_returns_one_line(self):
        d, invoice = self.inv()
        for q in ("6", "6"):
            try:
                d.create_return(quantities={d.lines.get(): Decimal(q)}, post_notes=False)
            except ValidationError as e:
                print("S1b second return:", e.messages)
        print("S1b notes", [(n.total(), n.posted) for n in Invoice.objects.filter(credits=invoice)])
        print("S1b returnable", d.lines.get().quantity_returnable())

    # ---- O298 hole from the author's Q2
    def test_s2_goodwill_then_goods(self):
        d, invoice = self.inv()
        note = self.typed_note(invoice, "1000")
        note.post()
        print("S2 after goodwill due", invoice.amount_due())
        try:
            d.create_return()
        except ValidationError as e:
            print("S2 return refused", e.messages)
        invoice.refresh_from_db()
        print("S2 due after return", invoice.amount_due())
        try:
            invoice.create_credit_note()
        except ValidationError as e:
            print("S2 credit refused", e.messages)
        print("S2 due after credit", invoice.amount_due(), [(n.total(), n.posted) for n in Invoice.objects.filter(credits=invoice)])

    def test_s2b_draft_goods_then_goodwill_both_post(self):
        d, invoice = self.inv()
        d.create_return(post_notes=False)
        goods = Invoice.objects.get(credits=invoice, posted=False, lines__credits_line__isnull=False)
        goods.post()
        try:
            self.typed_note(invoice, "1000").post()
        except ValidationError as e:
            print("S2b goodwill post refused", e.messages)
        print("S2b due", invoice.amount_due())

    def test_s3_note_variants_via_api(self):
        d, invoice = self.inv()
        ar = self.client_as("AR Manager")
        other = Account.objects.create(code="1101", name="AR2", account_type=AccountType.ASSET)
        base = {"customer": invoice.customer_id, "credits": invoice.pk, "currency": invoice.currency_id,
                "receivable_account": invoice.receivable_account_id, "invoice_date": str(invoice.invoice_date)}
        for name, patch in (("plain", {}), ("other acct", {"receivable_account": other.pk}),
                            ("before", {"invoice_date": "2026-02-01"}),
                            ("year ahead", {"invoice_date": str(timezone.localdate() + datetime.timedelta(days=400))})):
            r = ar.post("/api/sales/invoices/", {**base, **patch}, format="json")
            print("S3", name, r.status_code, r.content[:160])
        # past the invoice: typed line over api on the plain draft
        plain = Invoice.objects.filter(credits=invoice).first()
        if plain:
            r = ar.post("/api/sales/invoice-lines/", {"invoice": plain.pk, "description": "x", "quantity": "1",
                                                      "unit_price": "5000", "revenue_account": self.revenue.pk}, format="json")
            print("S3 typed 5000 ->", r.status_code, r.content[:200])
            r = ar.post("/api/sales/invoice-lines/", {"invoice": plain.pk, "description": "x", "quantity": "1",
                                                      "unit_price": "1000", "revenue_account": self.revenue.pk}, format="json")
            print("S3 typed 1000 ->", r.status_code, r.content[:200])
            r = ar.post("/api/sales/invoice-lines/", {"invoice": plain.pk, "description": "y", "quantity": "1",
                                                      "unit_price": "1", "revenue_account": self.revenue.pk}, format="json")
            print("S3 typed +1 ->", r.status_code, r.content[:200])
            r = ar.post(f"/api/sales/invoices/{plain.pk}/post_invoice/", {}, format="json")
            print("S3 post ->", r.status_code, r.content[:200])

    # ---- O294
    def commission_setup(self, taxed=True):
        from apps.accounting.models import Tax, TaxGroup
        from .models import CommissionBasis, CommissionPlan, SalesRep, commission_report
        group = TaxGroup.objects.create(code="VAT", name="VAT")
        acct = Account.objects.create(code="2100", name="VAT", account_type=AccountType.LIABILITY)
        vat = Tax.objects.create(code="V20", name="V", rate=Decimal("20"), group=group, collected_account=acct, paid_account=acct)
        plan = CommissionPlan.objects.create(code="P", name="p", percent=Decimal("5"), basis=CommissionBasis.PAID)
        SalesRep.objects.create(party=self.rep, plan=plan)
        order = self.make_order("10", "100", rep=self.rep)
        for l in order.lines.all():
            l.taxes.set([vat])
        return self.bill(order), commission_report

    def test_s4_commission(self):
        invoice, report = self.commission_setup()
        p1 = self.receipt("400", on=datetime.date(2026, 3, 10)); self.allocate(p1, invoice, "400")
        p2 = self.receipt("800", on=datetime.date(2026, 4, 10)); self.allocate(p2, invoice, "800")
        print("S4 all", report())
        print("S4 april only", report(datetime.date(2026, 4, 1), datetime.date(2026, 4, 30)))
        print("S4 march only", report(datetime.date(2026, 3, 1), datetime.date(2026, 3, 31)))
        p2.void()
        print("S4 after void p2", report())
        # a credit note of half, after full payment
        p3 = self.receipt("800", on=datetime.date(2026, 4, 11)); self.allocate(p3, invoice, "800")
        cn = invoice.create_credit_note(quantities={invoice.lines.get(): Decimal("5")})
        print("S4 with credit note", report(), cn.total())

    # ---- O295
    def test_s5_dunning(self):
        for n, dd in (("A", 7), ("B", 30), ("C", 60)):
            DunningLevel.objects.create(name=n, days_overdue=dd)
        d, invoice = self.inv()
        due = invoice.due_date
        def sent():
            return list(DunningNotice.objects.filter(invoice=invoice).order_by("id").values_list("level__days_overdue", flat=True))
        run_dunning(as_of=due + datetime.timedelta(days=8), send=False)
        pay = self.receipt("1000", on=due + datetime.timedelta(days=9)); self.allocate(pay, invoice, "1000")
        run_dunning(as_of=due + datetime.timedelta(days=40), send=False)
        print("S5 paid then run", sent())
        pay.void()
        run_dunning(as_of=due + datetime.timedelta(days=41), send=False)
        print("S5 void then run day41", sent())
        run_dunning(as_of=due + datetime.timedelta(days=70), send=False)
        run_dunning(as_of=due + datetime.timedelta(days=71), send=False)
        print("S5 day70", sent())
        # as_of earlier than a sent level (re-run for a past day)
        run_dunning(as_of=due + datetime.timedelta(days=35), send=False)
        print("S5 past day", sent())

    # ---- O299
    def test_s6_margin_leaks(self):
        ApprovalPolicy.objects.create(code="P", name="Floor", min_margin_percent=Decimal("20"), max_discount_percent=Decimal("50"))
        order = SalesOrder.objects.create(customer=self.customer, order_date=datetime.date(2026, 3, 1), currency=self.usd)
        line = SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("10"),
                                             unit_price=Decimal("1"), revenue_account=self.revenue)
        ctrl = self.client_as("AR Manager")
        rep = self.client_as("Sales Rep")
        def show(tag, r):
            body = r.content.decode()
            print("S6", tag, r.status_code, "LEAK" if ("-300" in body or "300." in body) else "ok", body[:230])
        show("detail", rep.get(f"/api/sales/sales-orders/{order.pk}/"))
        show("list", rep.get("/api/sales/sales-orders/"))
        show("approval", rep.get(f"/api/sales/sales-orders/{order.pk}/approval/"))
        show("confirm", rep.post(f"/api/sales/sales-orders/{order.pk}/confirm/", {}, format="json"))
        approve = [a for a in ("approve",) ]
        show("approve as rep", rep.post(f"/api/sales/sales-orders/{order.pk}/approve/", {}, format="json"))
        # approved by controller (note stored) then rep reads
        r = ctrl.post(f"/api/sales/sales-orders/{order.pk}/approve/", {}, format="json")
        print("S6 ctrl approve", r.status_code, r.content[:150])
        order.refresh_from_db(); print("S6 approval_note:", order.approval_note)
        show("detail after approve", rep.get(f"/api/sales/sales-orders/{order.pk}/"))
        show("confirm after approve", rep.post(f"/api/sales/sales-orders/{order.pk}/confirm/", {}, format="json"))
        # confirmed: rep drops price further
        show("line patch", rep.patch(f"/api/sales/sales-order-lines/{line.pk}/", {"unit_price": "0.5"}, format="json"))
        show("line add", rep.post("/api/sales/sales-order-lines/", {"order": order.pk, "item": self.item.pk, "uom": self.uom.pk, "quantity": "1", "unit_price": "0.1", "revenue_account": self.revenue.pk}, format="json"))
        for url in ("/api/sales/quotations/", "/api/sales/approval-policies/"):
            show(url, rep.get(url))


from django.test import TransactionTestCase, tag


@tag("migration")
class MigEdges(TransactionTestCase):
    def test_edges(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        from apps.core.models import Currency, ExchangeRate, Party, PartyRole, PartyRoleAssignment

        ex = MigrationExecutor(connection)
        ex.migrate([("sales", "0077_one_opportunity_an_order")])
        try:
            apps = MigrationExecutor(connection).loader.project_state([("sales", "0077_one_opportunity_an_order")]).apps
            Cur = apps.get_model("core", "Currency"); Rate = apps.get_model("core", "ExchangeRate")
            Party_ = apps.get_model("core", "Party"); Role = apps.get_model("core", "PartyRoleAssignment")
            SO = apps.get_model("sales", "SalesOrder")
            inr = Cur.objects.create(code="INR", name="R", is_base=True)
            eur = Cur.objects.create(code="EUR", name="E")
            Rate.objects.create(currency=eur, rate=Decimal("90.123456"), valid_from=datetime.date(2026, 1, 1))
            Rate.objects.create(currency=eur, rate=Decimal("95"), valid_from=datetime.date(2026, 3, 1))
            cust = Party_.objects.create(code="C", name="C")
            now = timezone.now()
            def mk(day, figs, appr=now):
                return SO.objects.create(customer=cust, order_date=day, currency=eur, approved_at=appr, approved_figures=figs).pk
            ids = {
                "on_boundary": mk(datetime.date(2026, 3, 1), {"total": "100.00", "margin": "50.00", "discounts": {}}),
                "day_before": mk(datetime.date(2026, 2, 28), {"total": "100.00", "margin": "50.00"}),
                "empty": mk(datetime.date(2026, 3, 1), {}),
                "nones": mk(datetime.date(2026, 3, 1), {"total": None, "margin": None}),
                "no_approval": mk(datetime.date(2026, 3, 1), {"total": "100.00", "margin": "50.00"}, appr=None),
                "negm": mk(datetime.date(2026, 3, 1), {"total": "0.01", "margin": "-300.00"}),
                "jsonnull": mk(datetime.date(2026, 3, 1), None),
            }
            MigrationExecutor(connection).migrate([("sales", "0078_approved_figures_in_base")])
            apps2 = MigrationExecutor(connection).loader.project_state([("sales", "0078_approved_figures_in_base")]).apps
            for k, pk in ids.items():
                print("MIG", k, apps2.get_model("sales", "SalesOrder").objects.get(pk=pk).approved_figures)
            MigrationExecutor(connection).migrate([("sales", "0077_one_opportunity_an_order")])
            for k, pk in ids.items():
                print("MIGBACK", k, apps.get_model("sales", "SalesOrder").objects.get(pk=pk).approved_figures)
        finally:
            ex = MigrationExecutor(connection)
            ex.migrate(ex.loader.graph.leaf_nodes())
