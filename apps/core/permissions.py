from rest_framework.permissions import (
    SAFE_METHODS,
    BasePermission,
    DjangoModelPermissions,
)


class ModelPermissions(DjangoModelPermissions):
    """
    DjangoModelPermissions, with reading guarded too.

    DRF's own class asks nothing of a GET beyond being logged in, so an
    operator whose only role was Employee Self Service could list every
    payslip, every employee and the whole general ledger. Found in
    review, not by any test: every test that read did so as a superuser.
    Reading now takes the model's view permission, as adding takes add.
    """

    perms_map = {
        **DjangoModelPermissions.perms_map,
        "GET": ["%(app_label)s.view_%(model_name)s"],
        "HEAD": ["%(app_label)s.view_%(model_name)s"],
    }

    def has_permission(self, request, view):
        """
        An action that names its permission takes that, and the right to
        see what it acts on — not the right to add one.

        DRF maps every POST to `add_`, so posting a pay run took
        `hr.add_payrun` on top of `hr.post_payrun`, and paying a payslip
        took `hr.add_payslip`, which means nothing: nobody adds payslips.
        A role that only posts could not, and the cure was to let the
        poster prepare too, which is the separation the roles exist for.

        An action that writes and names nothing takes the right to change
        the model, never DRF's add_. Fifteen did: an Inspector holding
        add_calibration voided the failed calibration that had made their
        own inspection suspect, and anyone who could ask for leave
        cancelled anyone's. Every one is meant to be named (the audit's
        "action without a declared permission"); this is the floor for
        the one that is not.

        A tuple names rights any one of which will do, where the record
        decides the rest: leave is cancelled by the person (who may ask
        for leave) or by whoever decides it.
        """
        action = getattr(view, "action", None)
        required = getattr(view, "action_permission_map", {}).get(action)
        if isinstance(required, dict):
            required = required.get(request.method)
        if not required and request.method not in SAFE_METHODS and is_extra_action(view, action):
            required = self._changing(view)
        if not required:
            return super().has_permission(request, view)
        user = request.user
        if not user or not user.is_authenticated:
            return False
        model = self._queryset(view).model
        return user.has_perms(self.get_required_permissions("GET", model)) and holds(user, required)

    def _changing(self, view):
        meta = self._queryset(view).model._meta
        return f"{meta.app_label}.change_{meta.model_name}"


def holds(user, required):
    """Whether `user` holds `required`: one permission, or any one of a tuple of them."""
    names = required if isinstance(required, tuple) else (required,)
    return any(user.has_perm(name) for name in names)


def is_extra_action(view, action):
    """Whether `action` is one of the viewset's own @action methods rather than list, create, update..."""
    return bool(action) and hasattr(getattr(view, action, None), "mapping")


class ActionPermission(BasePermission):
    """
    Enforces segregation of duties for custom actions (post, reverse,
    credit_note, approve, ...) on top of DjangoModelPermissions' standard
    add/change/delete checks.

    A ViewSet opts in by declaring `action_permission_map`, e.g.:

        action_permission_map = {"post_invoice": "sales.post_invoice"}

    One whose record decides between people takes any of several, as a tuple:

        action_permission_map = {"cancel": ("hr.add_leaverequest", "hr.decide_leaverequest")}

    An action that both answers a read and takes a write maps each method:

        action_permission_map = {"values": {"GET": "sales.view_priceindexvalue",
                                            "POST": "sales.add_priceindexvalue"}}

    Actions not listed are unaffected by this class (ModelPermissions asks
    the model's change_ of one that writes, and every one that writes is
    to be listed). This is deliberately model-level, not object-level: it
    answers "can this user post invoices at all", not "can this user post
    THIS invoice" — the latter would need a way to tie a request.user to a
    specific Party/Employee, which doesn't exist yet.
    """

    def has_permission(self, request, view):
        required = getattr(view, "action_permission_map", {}).get(getattr(view, "action", None))
        if isinstance(required, dict):
            required = required.get(request.method)
        if not required:
            return True
        return bool(request.user and holds(request.user, required))


class RequiredPermission(BasePermission):
    """
    For a view with no model behind it - a report, a board - that says
    what reading it takes in `required_permission`.

    Closed by default: a view that forgets to say refuses everybody,
    which somebody notices the same day, rather than serving everybody,
    which nobody notices at all. Six report endpoints were open to any
    login this way, the profit and loss and balance sheet among them.
    """

    def has_permission(self, request, view):
        needed = getattr(view, "required_permission", None)
        return bool(needed and request.user and request.user.has_perm(needed))

