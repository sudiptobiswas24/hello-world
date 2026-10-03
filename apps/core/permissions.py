from rest_framework.permissions import BasePermission


class ActionPermission(BasePermission):
    """
    Enforces segregation of duties for custom actions (post, reverse,
    credit_note, approve, ...) on top of DjangoModelPermissions' standard
    add/change/delete checks.

    A ViewSet opts in by declaring `action_permission_map`, e.g.:

        action_permission_map = {"post_invoice": "sales.post_invoice"}

    Actions not listed are unaffected by this class (DjangoModelPermissions
    still applies). This is deliberately model-level, not object-level: it
    answers "can this user post invoices at all", not "can this user post
    THIS invoice" — the latter would need a way to tie a request.user to a
    specific Party/Employee, which doesn't exist yet.
    """

    def has_permission(self, request, view):
        required = getattr(view, "action_permission_map", {}).get(getattr(view, "action", None))
        if not required:
            return True
        return bool(request.user and request.user.has_perm(required))
