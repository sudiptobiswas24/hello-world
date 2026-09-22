"""
Whether a batch may leave.

One invariant, in one place: a batch of something that must be
inspected does not go into a run or onto a lorry until somebody has
measured it and passed it. It is written here rather than in each
document for the same reason the negative-stock rule was pulled out of
four of them — a fifth copy is how the fifth copy starts.

**Not yet inspected is not passed.** The commonest way a quality module
turns into a filing cabinet is by treating an absent inspection as
consent: the plan exists, nobody has run it, and the goods ship anyway.
So the default answer for a mandatory plan is no.

The status is derived from the latest standing inspection rather than
stored on the lot. Voiding an inspection then hands the question back
to the one before it, or to nobody — which is "not yet inspected",
which is no. A stored flag would have had to remember to do that.
"""

from django.core.exceptions import ValidationError

from .models import Disposition, Inspection, InspectionPlan, ReleaseStatus


def plan_for(item):
    """The active inspection plan for an item, or None."""
    return item.inspection_plans.filter(is_active=True).first()


def latest_inspection(lot):
    """The inspection that currently speaks for this batch."""
    return Inspection.objects.filter(
        lot=lot, posted=True, voided_at__isnull=True
    ).order_by("-inspected_on", "-id").first()


def release_status(lot):
    """
    Where this batch stands.

    Rework and rejection are both held: a roll waiting to be re-wound is
    not stock anybody may draw on, however likely it is to pass the
    second time.
    """
    inspection = latest_inspection(lot)
    if inspection is None:
        return ReleaseStatus.UNINSPECTED
    if inspection.disposition in (Disposition.ACCEPT, Disposition.CONCESSION):
        return ReleaseStatus.RELEASED
    return ReleaseStatus.HELD


def check_released(item, lot, action="issue"):
    """
    Refuse a batch that has not been passed, where the item says it must
    be.

    An item with no plan, or an advisory one, passes straight through:
    most of what a plant moves is not inspected and this must not stand
    in its way.
    """
    plan = plan_for(item)
    if plan is None or not plan.is_mandatory:
        return
    if lot is None:
        raise ValidationError(
            f"{item} has a mandatory inspection plan, so it moves by batch and "
            f"this line does not name one. Nothing can {action} a quantity "
            "nobody can point at a measurement for."
        )
    status = release_status(lot)
    if status == ReleaseStatus.RELEASED:
        return
    if status == ReleaseStatus.UNINSPECTED:
        raise ValidationError(
            f"{lot} has not been inspected and {item} says it must be. Not yet "
            f"inspected is not passed, so it cannot {action} yet."
        )
    held = latest_inspection(lot)
    raise ValidationError(
        f"{lot} is held: {held.get_disposition_display().lower()} on "
        f"{held.inspected_on} ({held.number}). It cannot {action}."
    )
