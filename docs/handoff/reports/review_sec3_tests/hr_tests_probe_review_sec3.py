"""review_sec3 probes: O131, a deleted record's history read by someone who could not read the record."""

import datetime

from django.contrib.auth.models import Group, User
from rest_framework.test import APIClient

from apps.hr import tests_people
from apps.hr.appraisals import Appraisal


class O131(tests_people.PeopleTestCase):
    def inspector(self, name="insp"):
        user = User.objects.create_user(name)
        user.groups.add(*Group.objects.filter(name__in=["Employee Self Service", "Quality Inspector"]))
        return self.as_(user)

    def gone_history(self, url, history, make):
        record = make()
        insp = self.inspector()
        hr = self.as_(self.hr)
        standing = insp.get("/api/core/history/", history(record)).status_code
        self.assertEqual(hr.delete(f"{url}{record.pk}/").status_code, 204)
        return standing, insp.get("/api/core/history/", history(record)), hr.get("/api/core/history/", history(record)).status_code

    def test_a_deleted_expense_claims_history_is_not_read_by_a_colleague(self):
        standing, gone, hr = self.gone_history(
            "/api/hr/expense-claims/", lambda c: {"model": "hr.expenseclaim", "id": c.pk}, lambda: self.claim(self.riley))
        self.assertEqual((standing, gone.status_code, hr), (404, 404, 200),
                         f"standing {standing}; after delete the inspector reads: {gone.content[:200]}")

    def test_a_deleted_appraisals_history_is_not_read_by_a_colleague(self):
        def make():
            return Appraisal.objects.create(employee=self.riley, reviewer=self.manager,
                                            period_start=datetime.date(2026, 1, 1), period_end=datetime.date(2026, 6, 30))
        standing, gone, hr = self.gone_history(
            "/api/hr/appraisals/", lambda a: {"model": "hr.appraisal", "id": a.pk}, make)
        self.assertEqual((standing, gone.status_code, hr), (404, 404, 200),
                         f"standing {standing}; after delete the inspector reads: {gone.content[:200]}")

    def test_which_viewsets_narrow_by_login_with_no_say_about_a_gone_record(self):
        """Static survey: get_queryset names request.user, and no reads_only_some on the class."""
        import inspect

        from rest_framework.generics import GenericAPIView

        from apps.core.tests_probe_review_sec3 import routes

        seen, missing = set(), []
        for path, cls in routes():
            if cls in seen or not hasattr(cls, "get_queryset") or cls.get_queryset is GenericAPIView.get_queryset:
                continue
            seen.add(cls)
            try:
                source = "".join(inspect.getsource(klass.get_queryset) for klass in cls.__mro__
                                 if "get_queryset" in vars(klass) and klass.__module__.startswith("apps."))
            except (OSError, TypeError):
                continue
            if "request.user" in source and "visible_parties" not in source and not hasattr(cls, "reads_only_some"):
                missing.append(f"{cls.__module__}.{cls.__name__}")
        print("NARROWS BY LOGIN, NO reads_only_some:", sorted(missing))
        self.assertEqual(sorted(missing), [])
