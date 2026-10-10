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
"""

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from .models import serialised


def may_give(actor, group):
    """Whether `actor` gives the role `group` on their own: by holding it, or as a superuser."""
    return actor.is_superuser or actor.groups.filter(pk=group.pk).exists()


def may_propose(actor):
    """Whether `actor` may propose a role they cannot give: whoever keeps logins."""
    return actor.has_perm("auth.change_user")


class ProposalStatus(models.TextChoices):
    PENDING = "pending", "Waiting for confirmation"
    CONFIRMED = "confirmed", "Confirmed"
    DECLINED = "declined", "Declined"


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

    def may_confirm(self, by):
        """Why `by` may not confirm it, or None."""
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
    proposal, _made = RoleProposal.objects.get_or_create(
        user=user, group=group, status=ProposalStatus.PENDING, defaults={"proposed_by": actor})
    return proposal


def waiting_for(user):
    """Proposals `user` could confirm now: pending, for a role they hold (any, for a superuser), not their own."""
    pending = RoleProposal.objects.filter(status=ProposalStatus.PENDING).exclude(user=user).exclude(proposed_by=user)
    return pending if user.is_superuser else pending.filter(group__in=user.groups.all())
