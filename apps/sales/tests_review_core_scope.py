"""
Review probes for fix_core's O130 (g22 review_core): scoped() keeps the rows that name no party. Asked of the API as
the Sales Rep (rep A carries Acme, rep B carries Beta, nobody carries Gamma) and the AR Manager.
"""
from apps.core.models import Address, Country

from .tests_reps import RepTestCase

ADDRESSES = "/api/core/addresses/"


class RowsThatNameNoPartyTests(RepTestCase):
    def setUp(self):
        super().setUp()
        india = Country.objects.create(code="IN", name="India")
        self.plant = Address.objects.create(party=None, line1="Plot 4, Uluberia", city="Howrah", country=india)
        self.beta_address = Address.objects.create(party=self.beta, line1="2 Mill Road", city="Howrah", country=india)
        self.acme_address = Address.objects.create(party=self.acme, line1="1 Mill Road", city="Howrah", country=india)

    def lines(self, client):
        return sorted(row["line1"] for row in self.codes(client, ADDRESSES))

    def test_a_rep_reads_the_plants_address_and_their_own_customers_but_not_anothers(self):
        self.assertEqual(self.lines(self.as_user(self.rep_a)), ["1 Mill Road", "Plot 4, Uluberia"])

    def test_the_ar_manager_reads_all_of_them(self):
        self.assertEqual(self.lines(self.as_role("AR Manager")),
                         ["1 Mill Road", "2 Mill Road", "Plot 4, Uluberia"])

    def test_a_rep_does_not_read_another_reps_customers_address_by_id(self):
        self.assertEqual(self.as_user(self.rep_a).get(f"{ADDRESSES}{self.beta_address.pk}/").status_code, 404)

    def test_a_rep_may_not_change_the_plants_own_address(self):
        # The plant's own address belongs to no customer, so no rep carries it: it was out of sight and so out
        # of reach before O130. In sight, "Only your own customers can be changed from here" must still hold.
        answer = self.as_user(self.rep_a).patch(f"{ADDRESSES}{self.plant.pk}/", {"line1": "Anywhere"}, format="json")
        self.plant.refresh_from_db()
        self.assertEqual((answer.status_code in (400, 403, 404), self.plant.line1), (True, "Plot 4, Uluberia"),
                         (answer.status_code, answer.content))

    def test_a_rep_may_not_make_an_address_that_names_no_party(self):
        answer = self.as_user(self.rep_a).post(
            ADDRESSES, {"line1": "Behind the mill", "city": "Howrah", "address_type": "billing"}, format="json")
        self.assertIn(answer.status_code, (400, 403), (answer.status_code, answer.content))

    def test_a_rep_may_not_detach_their_customers_address_from_the_customer(self):
        answer = self.as_user(self.rep_a).patch(f"{ADDRESSES}{self.acme_address.pk}/", {"party": None}, format="json")
        self.acme_address.refresh_from_db()
        self.assertEqual((answer.status_code in (400, 403), self.acme_address.party_id), (True, self.acme.pk),
                         (answer.status_code, answer.content))

    def test_a_rep_who_deletes_the_plants_address_gets_a_refusal_not_a_crash(self):
        # The Sales Rep role holds no delete permission on addresses: 403, whoever is in sight.
        answer = self.as_user(self.rep_a).delete(f"{ADDRESSES}{self.plant.pk}/")
        self.assertEqual(answer.status_code, 403, answer.content)
