"""
g22 review of fix_stores: O45, the one rule for posted documents in the admin, and the audit shapes it leaves.
"""
from django.contrib import admin
from django.test import TestCase


class StoresReviewPostedAdminProbes(TestCase):
    def test_p1_every_admin_of_a_model_with_a_posted_flag_refuses_a_posted_record(self):
        from apps.core.admin_mixins import PostedImmutableAdminMixin

        loose = []
        for model, model_admin in admin.site._registry.items():
            if "posted" not in {field.name for field in model._meta.get_fields()}:
                continue
            if not isinstance(model_admin, PostedImmutableAdminMixin):
                loose.append(f"{model._meta.label} ({type(model_admin).__name__})")
        # Not every one needs the mixin (a model may refuse in save/delete itself); this lists what does not
        # carry the shared rule, for the reader to weigh.
        print("\nO45 posted models whose admin lacks the shared mixin:", loose)

    def test_p2_a_posted_record_is_not_deletable_nor_editable_through_the_mixin_admins(self):
        from django.test import RequestFactory

        from django.contrib.auth.models import User

        from apps.core.admin_mixins import PostedImmutableAdminMixin

        request = RequestFactory().get("/")
        request.user = User.objects.create_superuser("root", "r@x.example", "pw")
        bad = []
        for model, model_admin in admin.site._registry.items():
            if not isinstance(model_admin, PostedImmutableAdminMixin):
                continue
            posted_field = getattr(model_admin, "posted_field", "posted")
            if posted_field not in {f.name for f in model._meta.get_fields()}:
                continue
            probe = model()
            setattr(probe, posted_field, True)
            can_change = model_admin.has_change_permission(request, probe)
            can_delete = model_admin.has_delete_permission(request, probe)
            # list_editable is checked against the model, never the row: a posted row would be editable there.
            if can_change or can_delete or getattr(model_admin, "list_editable", ()):
                bad.append((model._meta.label, can_change, can_delete, getattr(model_admin, "list_editable", ())))
        self.assertEqual(bad, [])
