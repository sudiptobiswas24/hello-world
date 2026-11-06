"""
What a rep limited to their own customers reads of costing: the cost
sheets of enquiries and of their own customers' quotations, since a
priced sheet shows what its customer was quoted; quotations priced only
for their own customers; an order's profitability only for their own
customers' orders. The rest reads as if it were not there. The sales
manager costs the sack; the rep quotes it. Whoever reads orders but not
costs reads no margin.
"""

from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.hr.models import Employee
from apps.sales.models import CustomerProfile, Quotation, SalesOrder, SalesOrderLine, SalesRep

from .quoting import cost, quote
from .tests_quoting import DAY, QuotingTestCase


class RepScopeTestCase(QuotingTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.mine = self.customer("C1", "Safe Agri")
        self.theirs = self.customer("C2", "Bharat Cement")
        self.rep = self.rep_carrying("anita", self.mine)
        self.rep_carrying("ravi", self.theirs)

    def customer(self, code, name):
        party = Party.objects.create(code=code, name=name)
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.CUSTOMER)
        return party

    def rep_carrying(self, username, customer):
        user = User.objects.create_user(username)
        user.groups.add(Group.objects.get(name="Sales Rep"))
        person = Party.objects.create(code=f"REP-{username}", name=username.title())
        PartyRoleAssignment.objects.create(party=person, role=PartyRole.EMPLOYEE)
        Employee.objects.create(party=person, employee_number=f"E-{username}", hire_date=DAY, user=user)
        SalesRep.objects.create(party=person)
        CustomerProfile.objects.create(party=customer, sales_rep=person)
        client = APIClient()
        client.force_authenticate(user)
        return client

    def quoted_for(self, customer):
        sheet = cost(self.sack, Decimal("50000"), DAY)
        quote(sheet, Quotation.objects.create(customer=customer, quotation_date=DAY), [])
        return sheet


class CostSheetTests(RepScopeTestCase):
    def test_a_rep_reads_enquiries_and_their_own_customers_prices_only(self):
        enquiry = cost(self.sack, Decimal("50000"), DAY)
        ours = self.quoted_for(self.mine)
        others = self.quoted_for(self.theirs)
        seen = {row["id"] for row in self.rep.get("/api/manufacturing/cost-sheets/").json()}
        self.assertEqual(seen, {enquiry.pk, ours.pk})
        self.assertEqual(self.rep.get(f"/api/manufacturing/cost-sheets/{others.pk}/").status_code, 404)

    def test_a_rep_prices_only_their_own_customers_quotations(self):
        sheet = cost(self.sack, Decimal("50000"), DAY)
        theirs = Quotation.objects.create(customer=self.theirs, quotation_date=DAY)
        refused = self.rep.post(f"/api/manufacturing/cost-sheets/{sheet.pk}/quote/",
                                {"quotation": theirs.pk, "taxes": []}, format="json")
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertIn("quotation", refused.json())
        self.assertFalse(theirs.lines.exists())
        ours = Quotation.objects.create(customer=self.mine, quotation_date=DAY)
        made = self.rep.post(f"/api/manufacturing/cost-sheets/{sheet.pk}/quote/",
                             {"quotation": ours.pk, "taxes": []}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        self.assertEqual(made.json()["unit_price"], "15.37")


    def test_the_sales_manager_costs_the_sack_and_a_rep_does_not(self):
        manager = User.objects.create_user("meena")
        manager.groups.add(Group.objects.get(name="AR Manager"))
        client = APIClient()
        client.force_authenticate(manager)
        body = {"specification": self.sack.pk, "quantity": "50000", "costed_on": DAY.isoformat()}
        made = client.post("/api/manufacturing/cost-sheets/", body, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        self.assertEqual(made.json()["quoted_price"], "15.37")
        self.assertEqual(self.rep.post("/api/manufacturing/cost-sheets/", body, format="json").status_code, 403)


class ProfitabilityTests(RepScopeTestCase):
    def order_for(self, customer):
        order = SalesOrder.objects.create(customer=customer, order_date=DAY)
        item = self.sack.bag_item
        SalesOrderLine.objects.create(order=order, item=item, uom=item.uom, quantity=Decimal("1000"),
                                      unit_price=Decimal("15.37"))
        return order

    def test_a_rep_reads_the_margins_on_their_own_customers_orders_only(self):
        ours, others = self.order_for(self.mine), self.order_for(self.theirs)
        self.assertEqual(len(self.rep.get("/api/manufacturing/order-profitability/", {"order": ours.pk}).json()), 1)
        self.assertEqual(self.rep.get("/api/manufacturing/order-profitability/", {"order": others.pk}).json(), [])

    def test_whoever_reads_orders_but_not_costs_reads_no_margin(self):
        ours = self.order_for(self.mine)
        for role in ("Warehouse Staff", "Production Planner"):
            user = User.objects.create_user(role.replace(" ", "_").lower())
            user.groups.add(Group.objects.get(name=role))
            client = APIClient()
            client.force_authenticate(user)
            response = client.get("/api/manufacturing/order-profitability/", {"order": ours.pk})
            self.assertEqual(response.status_code, 403, (role, response.content))
