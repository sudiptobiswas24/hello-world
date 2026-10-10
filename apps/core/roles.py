"""
A role given by two people (O142, the owner's rule).

Whoever keeps logins (auth.change_user, the HR Admin) gives a role they
hold themselves directly. One they do not hold they only propose: it is
given when someone who already holds it, or a superuser, confirms it.
The proposer never confirms their own proposal, and nobody proposes or
confirms anything for their own login. Taking a role away, or switching
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
no longer trusted for a reset (`lapse_on_giving`). A proposal lapses
with its login switched off (O193) or its proposer no longer keeping
logins (O192): `RoleProposal.lapsed`, asked live, and marked by
`lapse_what_died` where the keeper's own actions switch either off.
"""

from django.conf import settings
from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from django.utils import timezone

from .models import serialised


def may_give(actor, group):
    """Whether `actor` gives the role `group` on their own: by holding it, or as a superuser."""
    return actor.is_superuser or actor.groups.filter(pk=group.pk).exists()


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
        if by.pk == self.user_id:
            return "Roles on your own login are someone else's to confirm."
        if by.pk == self.proposed_by_id:
            return f"You proposed {self.group}: someone else who holds it confirms it."
        if not may_give(by, self.group):
            return f"{self.group} is confirmed by someone who holds it, and you do not."
        return None

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


def give_or_propose(actor, user, group):
    """
    Give `group` to `user` if `actor` may (may_give), or propose it if
    `actor` keeps logins. The proposal, or None when the role was given
    (or was held already).
    """
    if actor.pk == user.pk:
        raise ValidationError("Roles on your own login are someone else's to give.")
    if may_give(actor, group):
        user.groups.add(group)
        return None
    if not may_propose(actor):
        raise ValidationError(f"{group} is given by someone who holds it, and you do not.")
    if user.groups.filter(pk=group.pk).exists():
        return None
    # One that lapsed (RoleProposal.lapsed) gives way to this keeper's.
    dead = RoleProposal.objects.filter(user=user, group=group, status=ProposalStatus.PENDING).first()
    if dead is not None and dead.lapsed():
        dead._decide(actor, ProposalStatus.LAPSED)
    proposal, _made = RoleProposal.objects.get_or_create(
        user=user, group=group, status=ProposalStatus.PENDING, defaults={"proposed_by": actor})
    return proposal


def waiting_for(user):
    """Proposals `user` could confirm now: pending, for a role they hold (any, for a superuser), not their own."""
    pending = (RoleProposal.objects.filter(status=ProposalStatus.PENDING, user__is_active=True,
                                           proposed_by__in=keepers())
               .exclude(user=user).exclude(proposed_by=user))
    return pending if user.is_superuser else pending.filter(group__in=user.groups.all())


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
    A login's password or email as someone other than its person set it
    (O157): they know it. `value` is what was set (the password's hash,
    or the address); once the login's own differs, nobody else knows it.
    """

    PASSWORD, EMAIL = "password", "email"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="kept_credentials")
    kind = models.CharField(max_length=8, choices=[(PASSWORD, "Password"), (EMAIL, "Email")])
    value = models.CharField(max_length=254)
    set_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    set_at = models.DateTimeField(default=timezone.now)
    # Given a role `set_by` could not give: a password made unusable, an email not trusted for a reset.
    lapsed = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "kind"], name="one_kept_credential_per_kind")]

    def __str__(self):
        return f"{self.user}'s {self.kind}, set by {self.set_by}"

    def in_force(self, user):
        """Whether the login's own is still what `set_by` set."""
        if self.kind == self.PASSWORD:
            return user.password == self.value
        return bool(self.value) and user.email.lower() == self.value.lower()


def record_credential(user, kind, by):
    """`by` set `user`'s password or email (KeptCredential.PASSWORD or EMAIL): kept, unless it is their own."""
    if by.pk == user.pk:
        KeptCredential.objects.filter(user=user, kind=kind).delete()
        return
    value = user.password if kind == KeptCredential.PASSWORD else user.email
    KeptCredential.objects.update_or_create(user=user, kind=kind, defaults={
        "value": value, "set_by": by, "set_at": timezone.now(), "lapsed": False})


def lapse_on_giving(user, group):
    """
    `user` was given `group`: what someone who could not give it set on
    the login stops working. Its password is made unusable (whoever may
    keep the login issues the next one); its email is not trusted for a
    reset until someone who may keep the login sets it again.
    """
    for kept in KeptCredential.objects.filter(user=user, lapsed=False).select_related("set_by"):
        if not kept.in_force(user) or may_give(kept.set_by, group):
            continue
        if kept.kind == KeptCredential.PASSWORD:
            user.set_unusable_password()
            user.save(update_fields=["password"])
        kept.lapsed = True
        kept.save(update_fields=["lapsed"])


def trusted_for_reset(user):
    """Whether a reset mail may go to `user`'s email: not one set by someone since out-ranked (lapse_on_giving)."""
    return not KeptCredential.objects.filter(user=user, kind=KeptCredential.EMAIL, lapsed=True,
                                             value__iexact=user.email).exists()


@receiver(m2m_changed, sender=User.groups.through)
def _credentials_lapse_with_a_new_role(sender, instance, action, reverse, pk_set, **kwargs):
    """Every door that gives a role (a confirm, a grant, the admin, group.user_set) lapses what it must."""
    if action != "post_add" or not pk_set:
        return
    if reverse:
        for user in User.objects.filter(pk__in=pk_set):
            lapse_on_giving(user, instance)
    else:
        for group in Group.objects.filter(pk__in=pk_set):
            lapse_on_giving(instance, group)
