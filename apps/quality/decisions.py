"""
Who is told when quality waits for a decision, and who after.

A failure under an advisory plan is not rejected by the system: the batch
is held and the Quality Manager decides (RISKS.md, "Decisions taken", 22).
This is the telling. Each person gets a to-do on their own list, linked to
the record and due the next working day, and a mail where mail is set up.
A mail that did not go is said, never passed over: a manager "told" by a
mail nobody sent is told nothing, and the batch waits on a decision nobody
knows is theirs.

Written for any record, not only an inspection:
`tell_the_quality_manager(record, summary, link)` is what anything else
quality holds calls (a subcontract receipt that takes a held batch's hold).

The working day is the plain weekday, Monday to Friday, hr's default
pattern (apps/hr/calendars.py), not hr's calendar itself: quality depends
on core and inventory and on nothing else (apps/quality/models.py, first
lines), so that the modules that must ask it whether a batch may move
never find it asking them back. A plant holiday is therefore not seen: a
to-do posted the day before one falls due on it, and is escalated the day
after it rather than a working day later.
"""

import datetime
from dataclasses import dataclass, field

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.core.chatter import FollowUp, FollowUpKind, Note
from apps.core.models import lock_rows

QUALITY_MANAGER = "Quality Manager"
# Told that a batch is held for the Quality Manager, for their own planning.
FOR_INFORMATION = ("Production Supervisor", "Stores Manager")
WORKING_DAYS = {1, 2, 3, 4, 5}  # ISO weekdays: hr's DEFAULT_WORKING_DAYS, "12345"


def next_working_day(day):
    """The first working day after `day`."""
    day += datetime.timedelta(days=1)
    while day.isoweekday() not in WORKING_DAYS:
        day += datetime.timedelta(days=1)
    return day


@dataclass
class Told:
    """Who was given a to-do, and what did not reach whom."""

    told: list = field(default_factory=list)  # usernames given a to-do
    not_mailed: dict = field(default_factory=dict)  # username: why no mail went
    not_told: dict = field(default_factory=dict)  # username: why no to-do could be given
    nobody: str = ""  # said when the role is held by no active login at all

    @classmethod
    def together(cls, *each):
        """One `Told` of several, so that one reason is said once with everyone it kept a mail from."""
        merged = cls()
        for told in each:
            merged.told += told.told
            merged.not_mailed.update(told.not_mailed)
            merged.not_told.update(told.not_told)
            merged.nobody = merged.nobody or told.nobody
        return merged

    def failures(self):
        """The sentences for what did not reach someone, one per reason, naming who."""
        said = []
        for heading, by_person in (("Not given a to-do", self.not_told), ("Not mailed", self.not_mailed)):
            reasons = {}
            for username, reason in by_person.items():
                reasons.setdefault(reason, []).append(username)
            said.extend(f"{heading}: {', '.join(names)} ({reason.rstrip('.')})."
                        for reason, names in reasons.items())
        if self.nobody:
            said.insert(0, self.nobody)
        return said


def logins_in(*groups):
    return get_user_model().objects.filter(is_active=True, groups__name__in=groups).distinct().order_by("username")


def _mail(person, record, subject, body, by):
    """
    Mail one person once what told them is saved: '' when it is to go, else
    why it will not.

    Sent on commit, never before. A station posts an inspection inside its
    doff's own transaction, and a refusal later in that transaction left a
    mail about a posting that never stood. What can be known now is said
    now (no address, no mail server); a mail the server refuses after the
    commit is written on the record, the answer having gone by then.
    """
    from django.core.mail import get_connection

    from apps.core.mail import NOT_SET_UP, NotConfiguredBackend, deliver

    if not person.email:
        return "the login has no email address"
    if isinstance(get_connection(), NotConfiguredBackend):
        return NOT_SET_UP
    address, username = person.email, person.get_username()
    kind = ContentType.objects.get_for_model(type(record))

    def send():
        # Anything at all: the posting has stood by now, and one mail failing
        # must neither answer it with an error nor stop the mails after it.
        try:
            deliver(record, address, subject, body or subject, user=by, what="To-do")
        except Exception as failed:  # noqa: BLE001
            said = " ".join(failed.messages) if isinstance(failed, ValidationError) else str(failed)
            Note.objects.create(content_type=kind, object_id=record.pk,
                                body=f"The mail of the to-do to {username} did not go: {said}",
                                created_by=by, updated_by=by)

    transaction.on_commit(send)
    return ""


def tell(people, record, summary, due_on, link="", by=None, body=""):
    """
    Give each of `people` a to-do about `record`, due `due_on`, and mail
    each where mail is set up. Returns a `Told`. A person who cannot open
    the record is named, not given a to-do on a list they cannot act on.
    """
    told = Told()
    kind = ContentType.objects.get_for_model(type(record))
    for person in people:
        username = person.get_username()
        try:
            with transaction.atomic():
                FollowUp.objects.create(
                    content_type=kind, object_id=record.pk, kind=FollowUpKind.TODO,
                    summary=summary[:255], note=body, due_on=due_on, assigned_to=person,
                    link=link, created_by=by, updated_by=by,
                )
        except ValidationError as refused:
            told.not_told[username] = " ".join(refused.messages)
            continue
        told.told.append(username)
        reason = _mail(person, record, summary, body, by)
        if reason:
            told.not_mailed[username] = reason
    return told


def tell_the_quality_manager(record, summary, link="", by=None, body="", due_on=None, barred=()):
    """
    Every active login in the Quality Manager role: a to-do about `record`
    (due the next working day unless `due_on`), and a mail. Returns a
    `Told`: `told`, the usernames given a to-do; `not_mailed`, each one no
    mail reached and why; `nobody`, said when no login may decide it.

    `barred`: pks of logins who may not decide `record` (they raised it);
    not told to decide, and named in `nobody` when they are all there is.
    """
    holders = list(logins_in(QUALITY_MANAGER))
    people = [person for person in holders if person.pk not in barred or person.is_superuser]
    told = tell(people, record, summary, due_on or next_working_day(timezone.localdate()),
                link=link, by=by, body=body)
    if not holders:
        told.nobody = ("Nobody signs in as Quality Manager, so nobody has been told to decide: "
                       "tell whoever looks after the logins.")
    elif not people:
        names = ", ".join(person.get_username() for person in holders)
        told.nobody = (f"Nobody who may decide it has been told: {names} raised or changed it, and "
                       "somebody else decides. Tell whoever looks after the logins.")
    return told


@dataclass
class Posting:
    """What posting a failure that waits said, to the inspector and to the record."""

    deciders: Told
    informed: Told
    warning: str


def _failed(inspection):
    return ", ".join(str(row["characteristic"]) for row in inspection.failed_lines()) or "a reading"


def tell_on_posting(inspection, by=None):
    """
    The Quality Manager to decide, the Production Supervisor and the Stores
    Manager for information: a to-do each, due the next working day, and
    a mail. Returns the `Posting`, whose warning is the answer to the post.
    """
    from .models import Result
    from .release import held_inside

    link = f"/quality/inspections/{inspection.pk}"
    due = inspection.decision_due_on()
    inside = held_inside(inspection.lot) if inspection.result == Result.PASS else []
    if inside:
        names = ", ".join(str(lot) for lot in inside)
        body = (f"{inspection.lot} ({inspection.lot.item}) passed {inspection.number} on "
                f"{inspection.inspected_on:%d %b %Y}, but it is made from {names}, which quality "
                "holds, and its own pass cannot show a defect inside them: the batch is held until "
                "the Quality Manager decides to use it as is, rework it or reject it.")
        summary = f"Decide {inspection.number}: {inspection.lot} passed but is made from {names}, held"
        first = [f"{inspection.lot} passed ({inspection.number}) but is made from {names}, which "
                 "quality holds."]
    else:
        failed = _failed(inspection)
        body = (f"{inspection.lot} ({inspection.lot.item}) failed {inspection.number} on "
                f"{inspection.inspected_on:%d %b %Y}: {failed} outside the plan's limits. The plan is "
                "advisory, so the system has not rejected it: the batch is held until the Quality "
                "Manager decides to use it as is, rework it or reject it.")
        summary = f"Decide {inspection.number}: {inspection.lot} failed ({failed}) and is held"
        first = [f"{inspection.lot} FAILED: {failed} outside the plan's limits ({inspection.number})."]
    raised = inspection.raised_by()
    deciders = tell_the_quality_manager(
        inspection, summary, link=link, by=by, body=body, due_on=due, barred=raised)
    asked = set(deciders.told) | set(deciders.not_told)
    # A Quality Manager who raised it may not decide it: told, for information.
    informed = tell(
        logins_in(QUALITY_MANAGER, *FOR_INFORMATION).exclude(username__in=asked), inspection,
        f"For information: {inspection.lot} is held until the Quality Manager decides {inspection.number}",
        due, link=link, by=by, body=body)
    said = [
        *first,
        "The batch is HELD. Nobody may issue it, load it or ship it until the Quality Manager "
        "decides whether to use it as is, rework it or reject it.",
    ]
    if deciders.told:
        said.append(f"Told to decide, by a to-do due {due:%d %b %Y}: {', '.join(deciders.told)}.")
    if informed.told:
        said.append(f"Told for information: {', '.join(informed.told)}.")
    said.extend(Told.together(deciders, informed).failures())
    return Posting(deciders, informed, " ".join(said))


def _what_waits(inspection):
    """What an inspection waiting for the Quality Manager is about: a failure, or a recall (O207)."""
    if inspection.recalled_by_id is not None:
        return (f"was recalled by {inspection.number}: it is made from {inspection.recalled_by.lot}, "
                f"held by {inspection.recalled_by.number}")
    return f"failed {inspection.number} ({_failed(inspection)})"


def tell_the_administrator(inspection, today):
    """Every active superuser: a to-do due today that the decision is overdue, and a mail."""
    people = list(get_user_model().objects.filter(is_active=True, is_superuser=True).order_by("username"))
    posted = timezone.localdate(inspection.posted_at) if inspection.posted_at else inspection.inspected_on
    summary = (f"Overdue: {inspection.number} has waited for the Quality Manager's decision since "
               f"{posted:%d %b %Y}; {inspection.lot} is held")
    told = tell(people, inspection, summary, today, link=f"/quality/inspections/{inspection.pk}",
                body=(f"{inspection.lot} {_what_waits(inspection)} and was due a "
                      f"decision on {inspection.decision_due_on():%d %b %Y}. Nobody has decided it."))
    if not people:
        told.nobody = "No active administrator login to tell."
    return told


def escalate_overdue(today=None):
    """Each failure left undecided past the next working day, to the administrator: [(inspection, Told)]."""
    from .models import Disposition, Inspection

    found = []
    for inspection in Inspection.objects.filter(
            posted=True, voided_at__isnull=True, disposition=Disposition.AWAITING,
            escalated_on__isnull=True).order_by("posted_at", "pk"):
        told = inspection.escalate(today)
        if told is not None:
            found.append((inspection, told))
    return found


@transaction.atomic
def close_to_dos(record, by, outcome):
    """
    Close every open to-do on `record` as done today by `by`, with
    `outcome`, and say so once on the record. With nobody to name (a void
    from code), each is closed as its own person's, and the outcome says why.
    """
    kind = ContentType.objects.get_for_model(type(record))
    waiting = list(FollowUp.objects.filter(content_type=kind, object_id=record.pk, done_on__isnull=True))
    if not waiting:
        return 0
    lock_rows(*waiting, refresh=False)
    still = FollowUp.objects.filter(pk__in=[row.pk for row in waiting], done_on__isnull=True)
    closed = still.update(done_on=timezone.localdate(), done_by=by if by is not None else F("assigned_to"),
                          outcome=outcome, updated_by=by)
    if closed:
        Note.objects.create(content_type=kind, object_id=record.pk,
                            body=f"{closed} to-do(s) closed. {outcome}", created_by=by, updated_by=by)
    return closed
