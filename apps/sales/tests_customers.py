"""
A new customer opened in one go, asked of the API as the people who do it.

  Accounts (AR Manager, keeping GST too) opens Konkan Fertilisers: who they are, a billing
  and a shipping address, a buyer, their GSTIN and their terms. All of it
  is there afterwards, and the history says who made it.
  The same with a GSTIN that fails its check: nothing is left, not even
  the party, and the refusal says which section.
  A rep opens one with an address: it is theirs. A rep who also fills in
  the terms is refused before anything is made: the credit limit is not
  theirs to set. Nor is the GSTIN the AR Manager's alone: tax standing is
  the GST Officer's.
"""

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError

from apps.accounting.models import PartyTaxProfile
from apps.core.history import EventKind, RecordEvent
from apps.core.models import Address, Contact, Country, Party, PartyRole

from .models import CustomerProfile, Industry
from .models import revenue_report
from .tests_reps import RepTestCase, party

CUSTOMERS = "/api/sales/customers/"


class NewCustomerTestCase(RepTestCase):
    def setUp(self):
        super().setUp()
        self.india = Country.objects.create(code="IN", name="India")
        self.carrier = party("SRW", PartyRole.VENDOR)

    def accounts(self):
        user = User.objects.create_user("accounts")
        user.groups.add(*Group.objects.filter(name__in=["AR Manager", "GST Officer"]))
        return self.as_user(user)

    def konkan(self, **sections):
        return {
            "party": {"code": "KFL", "name": "Konkan Fertilisers", "cin": "u24120mh2001plc131234",
                      "website": "https://konkanfert.example"},
            "addresses": [
                {"address_type": "billing", "line1": "Nariman Point", "city": "Mumbai", "country": self.india.pk},
                {"address_type": "shipping", "line1": "MIDC Plot 7", "city": "Ratnagiri", "country": self.india.pk},
            ],
            "contacts": [{"first_name": "Anil", "last_name": "Desai", "job_title": "Purchase manager"}],
            "tax": {"gstin": "27AABCD1234E1Z8"},
            "terms": {"industry": "fertiliser", "freight_terms": "for_destination", "transporter": self.carrier.pk,
                      "sacks_per_bale": 500, "credit_limit": "800000.00"},
            **sections,
        }


class AccountsOpensACustomerTests(NewCustomerTestCase):
    def test_every_section_is_there_afterwards(self):
        response = self.accounts().post(CUSTOMERS, self.konkan(), format="json")
        self.assertEqual(response.status_code, 201, response.content)
        made = Party.objects.get(code="KFL")
        self.assertEqual(response.json()["id"], made.pk)
        self.assertEqual(made.cin, "U24120MH2001PLC131234")
        self.assertEqual(list(made.role_assignments.values_list("role", flat=True)), [PartyRole.CUSTOMER])
        self.assertEqual(sorted(Address.objects.filter(party=made).values_list("city", flat=True)),
                         ["Mumbai", "Ratnagiri"])
        self.assertEqual(Contact.objects.get(party=made).job_title, "Purchase manager")
        self.assertEqual(PartyTaxProfile.objects.get(party=made).gst_state, "27")
        terms = CustomerProfile.objects.get(party=made)
        self.assertEqual((terms.industry, terms.transporter, terms.sacks_per_bale),
                         (Industry.FERTILISER, self.carrier, 500))
        self.assertTrue(RecordEvent.objects.filter(object_id=str(made.pk), kind=EventKind.CREATED).exists())

    def test_a_refused_section_leaves_nothing_behind(self):
        response = self.accounts().post(
            CUSTOMERS, self.konkan(tax={"gstin": "27AABCD1234E1Z9"}), format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("tax", response.json())
        self.assertFalse(Party.objects.filter(code="KFL").exists())
        self.assertFalse(Address.objects.filter(city="Ratnagiri").exists())

    def test_every_refused_section_is_named_at_once(self):
        body = self.konkan(addresses=[{"address_type": "billing", "line1": "", "city": "Mumbai"}],
                           terms={"credit_hold": True})
        response = self.accounts().post(CUSTOMERS, body, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(sorted(response.json()), ["addresses.0", "terms"])
        self.assertEqual(response.json()["terms"], {"credit_hold_reason": ["Say why they are on hold."]})

    def test_who_they_are_is_asked_first(self):
        response = self.accounts().post(CUSTOMERS, self.konkan(party={"name": "No code"}),
                                                   format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("code", response.json()["party"])

    def test_the_tax_standing_is_not_the_ar_managers_alone(self):
        client = self.as_role("AR Manager")
        response = client.post(CUSTOMERS, self.konkan(), format="json")
        self.assertEqual(response.status_code, 403, response.content)
        self.assertIn("tax", response.json()["detail"])
        self.assertFalse(Party.objects.filter(code="KFL").exists())
        response = client.post(CUSTOMERS, self.konkan(tax=None), format="json")
        self.assertEqual(response.status_code, 201, response.content)

    def test_without_the_right_to_make_parties_nothing_is_made(self):
        response = self.as_role("Bookkeeper").post(CUSTOMERS, self.konkan(), format="json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Party.objects.filter(code="KFL").exists())


class RepOpensACustomerTests(NewCustomerTestCase):
    def test_what_a_rep_opens_is_theirs(self):
        body = self.konkan()
        del body["tax"], body["terms"]
        response = self.as_user(self.rep_a).post(CUSTOMERS, body, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        made = Party.objects.get(code="KFL")
        self.assertEqual(CustomerProfile.objects.get(party=made).sales_rep, self.rep_a.employee.party)
        self.assertEqual(Address.objects.filter(party=made).count(), 2)

    def test_the_terms_are_not_a_reps_to_set(self):
        response = self.as_user(self.rep_a).post(CUSTOMERS, self.konkan(tax=None), format="json")
        self.assertEqual(response.status_code, 403, response.content)
        self.assertIn("terms", response.json()["detail"])
        self.assertFalse(Party.objects.filter(code="KFL").exists())


class WhoTheyAreTests(NewCustomerTestCase):
    def test_a_cin_and_an_iec_have_their_shape(self):
        with self.assertRaisesMessage(ValidationError, "cin"):
            Party.objects.create(code="X1", name="X", cin="U24120MH2001PLC13123")
        with self.assertRaisesMessage(ValidationError, "iec"):
            Party.objects.create(code="X2", name="X", iec="AB-1234567")
        made = Party.objects.create(code="X3", name="X", iec="aabcd1234e")
        self.assertEqual(made.iec, "AABCD1234E")

    def test_a_group_does_not_contain_itself(self):
        group = Party.objects.create(code="GRP", name="Konkan Group")
        member = Party.objects.create(code="MEM", name="Konkan Fertilisers", parent=group)
        group.parent = member
        with self.assertRaisesMessage(ValidationError, "Konkan Group cannot belong to itself or to one of its own members"):
            group.save()

    def test_revenue_by_their_trade(self):
        CustomerProfile.objects.filter(party=self.acme).update(industry=Industry.CEMENT)
        self.customer = self.acme
        self.make_invoice().post()
        self.customer = self.beta  # no trade recorded
        self.make_invoice().post()
        rows = {row["key"]: row["net"] for row in revenue_report(group_by="industry")}
        self.assertEqual(set(rows), {"Cement", "Not set"})
        self.assertEqual(rows["Cement"], rows["Not set"])
