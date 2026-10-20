"""Audit 2, floor lane: which roles read what, over the manufacturing API. Prints; asserts the floor roles
cannot read costs or the books' figures (the setup_roles comments say they must not)."""
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

COSTS = ["cost-versions", "standard-costs", "material-rates", "stage-rates", "quote-policies",
         "cost-sheets", "order-profitability"]


class FloorRolesCannotReadCosts(TestCase):
    def setUp(self):
        call_command("setup_roles", verbosity=0)

    def as_role(self, role):
        user = User.objects.create_user(role.replace(" ", "_"))
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_floor_roles_do_not_read_costing(self):
        seen = {}
        for role in ("Station", "Production Supervisor", "Maintenance", "Warehouse Staff", "Quality Inspector"):
            client = self.as_role(role)
            for name in COSTS:
                code = client.get(f"/api/manufacturing/{name}/").status_code
                if code == 200:
                    seen.setdefault(role, []).append(name)
        self.assertEqual(seen, {}, "roles reading costs they were told not to")


class TabletReach(FloorRolesCannotReadCosts):
    def test_what_the_tablet_login_reads(self):
        from apps.manufacturing.urls import router

        client = self.as_role("Station")
        reach = [prefix for prefix, _view, _name in router.registry
                 if client.get(f"/api/manufacturing/{prefix}/").status_code == 200]
        print("TABLET READS", reach)
        sup = self.as_role("Production Supervisor")
        reach = [prefix for prefix, _view, _name in router.registry
                 if sup.get(f"/api/manufacturing/{prefix}/").status_code != 200]
        print("SUPERVISOR REFUSED", reach)
