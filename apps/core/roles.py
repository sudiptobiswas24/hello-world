"""
A role given by two people (O142, the owner's rule).

Whoever keeps logins (auth.change_user, the HR Admin) gives a role they
hold themselves directly. One they do not hold they only propose: it is
given when someone who already holds it, or a superuser, confirms it.
The proposer never confirms their own proposal, and nobody proposes or
confirms anything for their own login, nor through a login whose
password or email they set (`acts_as`, O195: HR made `puppet` with HR
Admin, knew its password, and as puppet proposed Bookkeeper for HR's own
login). A person's login is given only what the link to the person
would have taken (`refused_for_its_person`, O196). Taking a role away, or switching
a login off, needs nobody else (users_api).

`may_give` is the one place that says who gives a role on their own;
every other check about roles (making a login, changing one, setting
its password, confirming a proposal) asks it.

A password or email someone else set is theirs to sign in with (O157):
the keeper chose asha's first password, a Bookkeeper confirmed asha's
role, and the keeper signed in as asha and confirmed its own next
proposal. So who set them is kept (`KeptCredential`), and the moment a
login is given a role the setter could not give, by any door (the
`m2m_changed` receiver), the password stops working and the email is
no longer trusted for a reset (`lapse_on_giving`). Any right does it,
not only a role: a permission of its own or the admin site given in the
admin (O198). Who set a password is kept when it is set and ends at
its next change, never read off the hash, which is not kept; who made
the login is kept too, and no password change ends it (review F1, F3,
F8). A setter switched off or demoted knows nothing it set (review F2).
One set before the record began is kept with no setter and said in
words to whoever confirms a role for the login (`credentials_in_words`). A proposal lapses
with its login switched off (O193) or its proposer no longer keeping
logins (O192): `RoleProposal.lapsed`, asked live, and marked by
`lapse_what_died` where the keeper's own actions switch either off.
"""

from django.conf import settings
from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.db.models.signals import m2m_changed, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from .models import numbering_first, serialised


def may_give(actor, group):
    """Whether `actor` gives the role `group` on their own: by holding it, or as a superuser."""
    return actor.is_superuser or actor.groups.filter(pk=group.pk).exists()


def held_beyond(actor, user):
    """
    What `user` holds that `actor` could not give it, in words: its roles
    `actor` may not give (may_give), else a permission of its own beyond
    `actor`'s, else the admin site, else a superuser's standing. Empty
    for a superuser. Behind keeping a login (users_api.check_may_administer)
    and behind a credential lapsing (lapse_on_giving).
    """
    if actor.is_superuser:
        return []
    beyond = sorted(group.name for group in user.groups.all() if not may_give(actor, group))
    own = {f"{app_label}.{codename}" for app_label, codename in
           user.user_permissions.values_list("content_type__app_label", "codename")}
    if not beyond and own - actor.get_all_permissions():
        beyond = ["permissions of its own"]
    if not beyond and user.is_staff and not actor.is_staff:
        beyond = ["the admin site"]
    if not beyond and user.is_superuser:
        beyond = ["a superuser's standing"]
    return beyond


def may_propose(actor):
    """Whether `actor` may propose a role they cannot give: whoever keeps logins."""
    return actor.has_perm("auth.change_user")


def keepers():
    """The logins that may propose now (may_propose), as a query: active, and a superuser or holding auth.change_user."""
    held = Permission.objects.filter(codename="change_user", content_type__app_label="auth")
    return User.objects.filter(is_active=True).filter(
        Q(is_superuser=True) | Q(user_permissions__in=held) | Q(groups__permissions__in=held)).distinct()


class ProposalStatus(models.TextChoices):
    PENDING = "pending", "Waiting for confirmation"
    CONFIRMED = "confirmed", "Confirmed"
    DECLINED = "declined", "Declined"
    LAPSED = "lapsed", "Lapsed"


class RoleProposal(models.Model):
    """A role proposed for a login by whoever keeps logins, given once someone who holds it confirms."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="role_proposals")
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="proposals")
    status = models.CharField(max_length=10, choices=ProposalStatus.choices, default=ProposalStatus.PENDING)
    proposed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    proposed_at = models.DateTimeField(default=timezone.now)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-proposed_at", "-pk"]
        permissions = [("confirm_roleproposal", "Can confirm or decline a role proposed for a login")]
        constraints = [
            models.UniqueConstraint(fields=["user", "group"], condition=Q(status="pending"),
                                    name="one_pending_proposal_per_role"),
        ]

    def __str__(self):
        return f"{self.group} for {self.user}"

    def lapsed(self):
        """
        Why it can no longer be given, or None: its login is switched off
        (O193: a leaver was given the role), or whoever proposed it no
        longer keeps logins (O192: a dismissed keeper's proposals stayed
        live). Asked at the confirm itself, whatever switched either off.
        """
        user = User.objects.get(pk=self.user_id)
        if not user.is_active:
            return f"{user} is switched off: what was proposed for it lapsed with it."
        if not may_propose(User.objects.get(pk=self.proposed_by_id)):
            return f"{self.proposed_by} no longer keeps logins: what they proposed lapsed."
        return None

    def may_confirm(self, by):
        """Why `by` may not confirm it, or None."""
        said = self.lapsed()
        if said:
            return said
        user, proposer = User.objects.get(pk=self.user_id), User.objects.get(pk=self.proposed_by_id)
        if by.pk == user.pk:
            return "Roles on your own login are someone else's to confirm."
        if by.pk == proposer.pk:
            return f"You proposed {self.group}: someone else who holds it confirms it."
        # Nor through a login one of them signs in as (O195).
        for who in (user, proposer):
            if acts_as(who, by):
                return f"{who} made your login or set your password or email, and so signs in as you: someone else confirms it."
        if acts_as(user, proposer):
            return (f"{user} made the login of {proposer}, who proposed it, or set its password or email: proposed "
                    f"through a login "
                    f"{user} signs in as, it is {user}'s own proposal for themselves.")
        if not may_give(by, self.group):
            return f"{self.group} is confirmed by someone who holds it, and you do not."
        return refused_for_its_person(by, user, self.group)

    def may_decline(self, by):
        """Why `by` may not decline it, or None: whoever could confirm it, or its proposer withdrawing it."""
        if by.pk == self.user_id:
            return "Roles on your own login are someone else's to decide."
        if by.pk == self.proposed_by_id or may_give(by, self.group):
            return None
        return f"{self.group} is declined by someone who holds it, or withdrawn by whoever proposed it."

    def _decide(self, by, status):
        if self.status != ProposalStatus.PENDING:
            raise ValidationError(f"{self} is already {self.get_status_display().lower()}.")
        self.status = status
        self.decided_by = by
        self.decided_at = timezone.now()
        self.save(update_fields=["status", "decided_by", "decided_at"])

    @serialised("status")
    def confirm(self, by):
        """Give the role: the second person's yes."""
        said = self.may_confirm(by)
        if said:
            raise ValidationError(said)
        self._decide(by, ProposalStatus.CONFIRMED)
        # What the proposer set on the login lapses here (lapse_on_giving).
        self.user.groups.add(self.group)

    @serialised("status")
    def decline(self, by):
        """The reverse: the role is not given, and the proposal stays on record."""
        said = self.may_decline(by)
        if said:
            raise ValidationError(said)
        self._decide(by, ProposalStatus.DECLINED)


def who_knows(login):
    """
    The logins that can sign in as `login` (O195): whoever made it or set
    its password or email, still in their reach (KeptCredential), and
    whoever can sign in as them, and so on. Who made it is a fact its own
    password change does not end (review F1: puppet set its "own"
    password, which was HR's, and proposed for HR again).
    """
    known, frontier = set(), {login.pk}
    while frontier:
        rows = (KeptCredential.objects.filter(user__in=frontier, lapsed=False, set_by__isnull=False)
                .select_related("user", "set_by"))
        frontier = {row.set_by_id for row in rows if row.in_force(row.user) and _reached(row, row.user)}
        frontier -= known | {login.pk}
        known |= frontier
    return known


def acts_as(person, login):
    """Whether `person` is `login` or can sign in as it (who_knows): what is done through it is theirs."""
    return person.pk == login.pk or person.pk in who_knows(login)


def refused_for_its_person(actor, user, group):
    """
    Why `actor` may not give or confirm `group` for `user` while `user`
    is a person's login, or None (O196). HR linked an empty login to an
    employee, proposed Controller for it, and a Controller confirmed: a
    Controller login linked to a person, which the link itself would
    have refused (O194). The link rule for one role
    (users_api.linked_holding), asked here as the link asks it, so
    neither order gets round it.
    """
    from .scoping import party_of
    from .users_api import linked_holding

    person = party_of(user)
    if person is None or linked_holding(actor, group):
        return None
    return (f"{user} is the login of {person}: a person's login is given {group} by a superuser, or by whoever "
            f"keeps logins and holds {group}, as only they link a login holding it to a person.")


def give_or_propose(actor, user, group):
    """
    Give `group` to `user` if `actor` may (may_give), or propose it if
    `actor` keeps logins. The proposal, or None when the role was given
    (or was held already).
    """
    if actor.pk == user.pk:
        raise ValidationError("Roles on your own login are someone else's to give.")
    from .api_keys import refused_beyond_the_issuers

    # Said before anything is written; the m2m receiver below is the floor for every other door (review F5).
    beyond = refused_beyond_the_issuers(user, _rights_of(groups=[group]))
    if beyond:
        raise ValidationError(beyond)
    if acts_as(user, actor):
        raise ValidationError(f"{user} made your login or set your password or email, and so signs in as you: what "
                              f"you give or propose for {user} would be {user}'s own doing. Someone else does it.")
    if user.groups.filter(pk=group.pk).exists():
        return None
    if not may_give(actor, group) and not may_propose(actor):
        raise ValidationError(f"{group} is given by someone who holds it, and you do not.")
    said = refused_for_its_person(actor, user, group)
    if said:
        raise ValidationError(said)
    if may_give(actor, group):
        user.groups.add(group)
        return None
    # One that lapsed (RoleProposal.lapsed) gives way to this keeper's.
    dead = RoleProposal.objects.filter(user=user, group=group, status=ProposalStatus.PENDING).first()
    if dead is not None and dead.lapsed():
        dead._decide(actor, ProposalStatus.LAPSED)
    proposal, _made = RoleProposal.objects.get_or_create(
        user=user, group=group, status=ProposalStatus.PENDING, defaults={"proposed_by": actor})
    return proposal


def waiting_for(user):
    """
    Proposals `user` may confirm now (may_confirm): pending, for a role
    they hold (any, for a superuser), not their own, and none the
    two-person rule refuses them (review F7: the inbox listed proposals
    whose Confirm the screen then withheld).
    """
    pending = (RoleProposal.objects.filter(status=ProposalStatus.PENDING, user__is_active=True,
                                           proposed_by__in=keepers())
               .exclude(user=user).exclude(proposed_by=user))
    if not user.is_superuser:
        pending = pending.filter(group__in=user.groups.all())
    return RoleProposal.objects.filter(pk__in=[proposal.pk for proposal in pending.select_related("group")
                                               if proposal.may_confirm(user) is None])


def lapse_what_died(by, user):
    """
    Mark lapsed what can no longer be given (RoleProposal.lapsed): every
    pending proposal for `user` once it is switched off, and every one
    `user` proposed once they no longer keep logins. Asked after the
    keeper switches a login off or takes a role from it.
    """
    user = User.objects.get(pk=user.pk)
    dead = Q(user=user) if not user.is_active else Q(pk__in=[])
    if not may_propose(user):
        dead |= Q(proposed_by=user)
    RoleProposal.objects.filter(dead, status=ProposalStatus.PENDING).update(
        status=ProposalStatus.LAPSED, decided_by=by, decided_at=timezone.now())


class KeptCredential(models.Model):
    """
    Who other than a login's person can sign in as it (O157, O195): who
    made the login (LOGIN), who set its password (PASSWORD), who set its
    email (EMAIL). Facts kept at the event, never worked out again from
    the password's hash, and no copy of the hash is kept (O198; review F3
    and F8: a re-hash at sign-in, or a new SECRET_KEY, forgot the keeper).

    A PASSWORD row ends at the next change of the password, by anyone
    (`_a_saved_login`: the person's own through set_password or the
    password-change page, a keeper's, a reset); whoever issues the next
    one is kept for it. An EMAIL row holds while the address is the one
    set. A LOGIN row ends with neither: the maker chose the first password
    and could change it as the login, so the login's "own" password may
    be the maker's (review F1). Each lapses once what it reached is out of
    its setter's reach (lapse_on_giving: the login given what the setter
    could not give, which for a LOGIN row is the way back: someone other
    than its maker confirming a role above them; lapse_with_the_setter: a
    setter switched off or losing the right, review F2). A lapse makes the
    password unusable and the email untrusted for a reset. `set_by` None:
    set before who set it was kept (migration 0033), said in words to the
    confirmer, never lapsed.
    """

    PASSWORD, EMAIL, LOGIN = "password", "email", "login"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="kept_credentials")
    kind = models.CharField(max_length=8, choices=[(PASSWORD, "Password"), (EMAIL, "Email"), (LOGIN, "Login made")])
    # The address, for an email. Nothing for a password or a login: never the hash.
    value = models.CharField(max_length=254, blank=True)
    set_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                               related_name="+")
    set_at = models.DateTimeField(default=timezone.now)
    # Out of its setter's reach: a password made unusable, an email not trusted for a reset.
    lapsed = models.BooleanField(default=False)
    # Issued by whoever confirmed a role for the login (RoleProposal confirm), not by a keeper of logins.
    as_confirmer = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "kind"], name="one_kept_credential_per_kind")]

    def __str__(self):
        return f"{self.user}'s {self.kind}, set by {self.set_by or 'someone not on record'}"

    def in_force(self, user):
        """Whether it still stands: not lapsed, and an email still the login's address."""
        if self.lapsed:
            return False
        if self.kind == self.EMAIL:
            return bool(self.value) and user.email.lower() == self.value.lower()
        return True


def record_credential(user, kind, by, as_confirmer=False):
    """
    `by` set `user`'s password or email, or made the login (KeptCredential's
    kinds), as a keeper of logins or, `as_confirmer`, with a role's confirm:
    kept, unless it is their own.
    """
    if by is None or by.pk == user.pk:
        # Their own password ended the last one as it was saved (_a_saved_login); their own address ends its record.
        if kind == KeptCredential.EMAIL:
            KeptCredential.objects.filter(user=user, kind=kind).delete()
        return
    numbering_first()  # update_or_create() locks the row it finds: after the numbering row (O257)
    KeptCredential.objects.update_or_create(user=user, kind=kind, defaults={
        "value": user.email if kind == KeptCredential.EMAIL else "", "set_by": by, "set_at": timezone.now(),
        "lapsed": False, "as_confirmer": as_confirmer})


def _reached(kept, user):
    """
    Whether `kept`'s setter still reaches `user`. A maker: until the login
    holds what they could not give. A password or email: while its setter
    is switched on, still holds the right it was set with (keeping logins,
    or confirming roles) and could give everything the login holds
    (held_beyond). A keeper demoted or switched off knows none it set
    (review F2), as a proposal lapses with its proposer (O192).
    """
    setter = kept.set_by
    if kept.kind == KeptCredential.LOGIN:
        return not held_beyond(setter, user)
    right = setter.has_perm("core.confirm_roleproposal") if kept.as_confirmer else may_propose(setter)
    return setter.is_active and right and not held_beyond(setter, user)


def _lapse(kept, user):
    """
    What `kept.set_by` set or made stops working: a password is made
    unusable; a login's maker no longer has its email, unless someone else
    set it, trusted for a reset.
    """
    # A LOGIN row takes no password with it: the maker's first one, while it
    # stands, is its own PASSWORD row and lapses as that; one the login set
    # itself is the person's (must_set_own_password, O216 review F1).
    if kept.kind == KeptCredential.PASSWORD and user.has_usable_password():
        user.set_unusable_password()
        user.save(update_fields=["password"])
    if kept.kind == KeptCredential.LOGIN and user.email:
        email = KeptCredential.objects.filter(user=user, kind=KeptCredential.EMAIL).first()
        if email is None or not email.in_force(user) or email.set_by_id in (None, kept.set_by_id):
            numbering_first()  # update_or_create() locks the row it finds: after the numbering row (O257)
            KeptCredential.objects.update_or_create(user=user, kind=KeptCredential.EMAIL, defaults={
                "value": user.email, "set_by": kept.set_by, "set_at": timezone.now(), "lapsed": True})
    kept.lapsed = True
    kept.save(update_fields=["lapsed"])


def lapse_on_giving(user):
    """
    `user` was given a right, by any door: a role, a permission of its
    own, the admin site or a superuser's standing (O198). What a setter
    no longer reaches (_reached) stops working (_lapse).
    """
    with transaction.atomic():
        user = User.objects.get(pk=user.pk)
        # Every credential row of this login held first, in key order: a
        # lapse rewrites the email row and then the login's own, which out
        # of order is a deadlock with a second lapse at the same moment
        # (seen once the sentinel saw update(); g21).
        numbering_first()  # a raw row lock, after the company's numbering row (O257)
        held = list(KeptCredential.objects.select_for_update(no_key=True).filter(user=user).order_by("pk"))
        for kept in held:
            kept.refresh_from_db()  # a lapse before it may have rewritten this row
            if kept.lapsed or kept.set_by_id is None:
                continue
            if kept.in_force(user) and not _reached(kept, user):
                _lapse(kept, User.objects.get(pk=user.pk))


def lapse_with_the_setter(setter):
    """
    `setter` lost a right or was switched off (review F2): every password
    or email it set that it no longer reaches (_reached) stops working.
    What it made stays its (acts_as); a password the login set itself
    (must_set_own_password) was never this setter's, so lapses with nobody.
    """
    setter = User.objects.get(pk=setter.pk)
    for kept in (KeptCredential.objects.filter(set_by=setter, lapsed=False).exclude(kind=KeptCredential.LOGIN)
                 .select_related("user")):
        kept.set_by = setter
        if kept.in_force(kept.user) and not _reached(kept, kept.user):
            _lapse(kept, kept.user)
    # The API keys it issued do not lapse: the administrators are told (api_keys.issuer_out_of_reach, review F5).
    from .api_keys import issuer_out_of_reach

    issuer_out_of_reach(setter)


def must_set_own_password(user):
    """
    Whether `user` signs in with a password someone else set (a keeper's
    first password, an administrator's or a confirmer's, or one reset
    through an address someone else set) and so must set its own before
    it reaches anything (O216, review F1; second_factor.Gate). Nothing
    tells the person from the keeper typing as the login, so the keeper's
    password is never the one the login keeps: until it is changed it
    lapses with the keeper (lapse_with_the_setter, _reached); once changed
    it is the person's own (_a_saved_login ends the row) and lapses with
    nobody. A keeper who signs in first and changes it knows the result,
    and the person, whose first password no longer works, finds out.
    """
    if not user.is_authenticated or not user.has_usable_password():
        return False
    return (KeptCredential.objects.filter(user=user, kind=KeptCredential.PASSWORD, lapsed=False,
                                          set_by__isnull=False).exclude(set_by=user.pk).exists())


def password_reset_through_email(user):
    """
    `user` set a password through a reset link mailed to its email (O217):
    whoever set that address reads the mailbox, so knows the password, and
    is kept for it as they are for the address (its setter, as a keeper or
    a confirmer, or not on record). An address its person set is theirs,
    and so is the password: nobody else is kept for it, the maker included.
    """
    email = KeptCredential.objects.filter(user=user, kind=KeptCredential.EMAIL).first()
    if email is None or not email.in_force(user):
        KeptCredential.objects.filter(user=user, kind=KeptCredential.PASSWORD).delete()
        return
    numbering_first()  # update_or_create() locks the row it finds: after the numbering row (O257)
    KeptCredential.objects.update_or_create(user=user, kind=KeptCredential.PASSWORD, defaults={
        "value": "", "set_by": email.set_by, "set_at": timezone.now(), "lapsed": False,
        "as_confirmer": email.as_confirmer})


def trusted_for_reset(user):
    """Whether a reset mail may go to `user`'s email: not one set by someone since out of reach (_lapse)."""
    return not KeptCredential.objects.filter(user=user, kind=KeptCredential.EMAIL, lapsed=True,
                                             value__iexact=user.email).exists()


def credentials_in_words(proposal):
    """
    Who other than its person may sign in as the login a role is proposed
    for, and as the login that proposed it, in words, for whoever is asked
    to confirm it (O195, O198). What a keeper set lapses when the role is
    given; what was set before who set it was kept is only said, since it
    may be its person's own.
    """
    said = []
    for login in (proposal.user, proposal.proposed_by):
        for kept in KeptCredential.objects.filter(user=login).select_related("set_by").order_by("kind"):
            if not kept.in_force(login):
                continue
            what = {KeptCredential.PASSWORD: "password", KeptCredential.EMAIL: "email",
                    KeptCredential.LOGIN: "login"}[kept.kind]
            verb = "made" if kept.kind == KeptCredential.LOGIN else "set"
            if kept.set_by_id is None:
                if login.pk == proposal.user_id:
                    said.append(f"Who set {login}'s {what} is not on record: it was set before the office kept who "
                                "sets one, and whoever set it may still sign in as it. Issue the next password with "
                                "the confirm unless it was its person's own.")
            elif login.pk == proposal.user_id:
                said.append(f"{kept.set_by} {verb} {login}'s {what}; it stops working once a role {kept.set_by} "
                            "could not give is confirmed.")
            else:
                said.append(f"{kept.set_by} {verb} {login}'s {what}, so may sign in as {login}, who proposed this.")
    return said


def _rights_of(groups=(), permissions=()):
    rows = Permission.objects.filter(Q(group__in=groups) | Q(pk__in=permissions)).select_related("content_type")
    return {f"{row.content_type.app_label}.{row.codename}" for row in rows}


@receiver(m2m_changed, sender=User.groups.through)
@receiver(m2m_changed, sender=User.user_permissions.through)
@receiver(m2m_changed, sender=Group.permissions.through)
def _no_integration_given_past_its_keys_issuers(sender, instance, action, reverse, pk_set, **kwargs):
    """An integration's login is given nothing its live keys' issuers could not give (api_keys, review F5)."""
    from .api_keys import Integration, refused_beyond_the_issuers

    if action != "pre_add" or not pk_set:
        return
    if sender is Group.permissions.through:
        groups = list(Group.objects.filter(pk__in=pk_set)) if reverse else [instance]
        perms = set(pk_set) if not reverse else {instance.pk}
        logins = User.objects.filter(groups__in=groups, pk__in=Integration.objects.values("user")).distinct()
        rights = _rights_of(permissions=perms)
    elif reverse:
        logins = User.objects.filter(pk__in=pk_set).filter(pk__in=Integration.objects.values("user"))
        rights = (_rights_of(groups=[instance]) if sender is User.groups.through else _rights_of(permissions=[instance.pk]))
    else:
        logins = [instance]
        rights = (_rights_of(groups=pk_set) if sender is User.groups.through else _rights_of(permissions=pk_set))
    for login in logins:
        said = refused_beyond_the_issuers(login, rights)
        if said:
            raise ValidationError(said)


@receiver(pre_save, sender=User)
def _no_integration_made_a_superuser_past_its_keys_issuers(sender, instance, **kwargs):
    """A superuser's standing is every right: refused for an integration whose live key an issuer saw (review F5)."""
    from .api_keys import refused_beyond_the_issuers

    if instance.pk and instance.is_superuser and User.objects.filter(pk=instance.pk, is_superuser=False).exists():
        said = refused_beyond_the_issuers(instance, {"*"})
        if said:
            raise ValidationError(said)


@receiver(m2m_changed, sender=User.groups.through)
@receiver(m2m_changed, sender=User.user_permissions.through)
def _a_login_gaining_or_losing_a_right(sender, instance, action, reverse, pk_set, **kwargs):
    """Every door that gives or takes a role or a permission of its own (a confirm, a grant, the admin, user_set)."""
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    for user in (list(User.objects.filter(pk__in=pk_set or ())) if reverse else [instance]):
        if action == "post_add":
            lapse_on_giving(user)
        else:
            lapse_with_the_setter(user)


@receiver(m2m_changed, sender=Group.permissions.through)
def _a_role_widened_or_narrowed(sender, instance, action, reverse, pk_set, **kwargs):
    """A permission added to a role is given to everyone holding it; one taken, taken from them."""
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    groups = list(Group.objects.filter(pk__in=pk_set or ())) if reverse else [instance]
    holders = User.objects.filter(groups__in=groups).distinct()
    if action == "post_add":
        for user in holders.filter(kept_credentials__lapsed=False).distinct():
            lapse_on_giving(user)
    else:
        from .api_keys import live_keys

        for user in holders.filter(Q(pk__in=KeptCredential.objects.filter(lapsed=False).values("set_by"))
                                   | Q(pk__in=live_keys().values("created_by"))).distinct():
            lapse_with_the_setter(user)


@receiver(post_save, sender=User)
def _a_saved_login(sender, instance, created, update_fields=None, **kwargs):
    """
    A password changed: whoever set the last one no longer knows this one
    (whoever issued it records it next). Django's set_password leaves
    `_password` for save(); a re-hash at sign-in clears it first, so is no
    change (review F3). And the admin site, a superuser's standing or
    being switched off, given or taken by saving the login.
    """
    if created:
        return
    if getattr(instance, "_password", None) is not None:
        KeptCredential.objects.filter(user=instance, kind=KeptCredential.PASSWORD).delete()
    fields = None if update_fields is None else set(update_fields)
    if fields is None or fields & {"is_staff", "is_superuser"}:
        lapse_on_giving(instance)
    if fields is None or fields & {"is_active", "is_staff", "is_superuser"}:
        lapse_with_the_setter(instance)
