"""
What fell due overnight, for whoever it is due to. Each check is one
question the office used to have to remember to ask: a count, the screen
that lists exactly those, and the permissions that read it. The home
page shows a login its own rows; `morning_checks` mails them.

Nothing here runs by itself: cron runs the command each morning, and the
page asks when it opens. A check that has nothing to say is left out,
so an empty inbox is an empty inbox.
"""

import datetime
from dataclasses import dataclass
from typing import Callable

from django.utils import timezone

DAY = datetime.timedelta(days=1)
DRAFT_FOR = 2 * DAY
UNSIGNED_FOR = 7 * DAY


@dataclass(frozen=True)
class Check:
    key: str
    label: str
    permissions: tuple
    href: str
    count: Callable


def _maintenance_due(day, user=None):
    from apps.manufacturing.maintenance import due_now

    return len(due_now(as_of=day))


def _calibration_due(day, user=None):
    from apps.quality.calibration import due

    return len(due(within_days=30, on_date=day))


def _licences_due(day, user=None):
    from apps.core.licences import licences_due

    return len(licences_due(day))


def _contractor_licences(day, user=None):
    from apps.hr.contract_labour import licences_due

    return len(licences_due(within_days=30, on_date=day))


def _msme_at_risk(day, user=None):
    from apps.purchasing.msme import msme_bills

    return sum(1 for row in msme_bills(day - 365 * DAY, day, as_of=day) if row["at_risk"] > 0)


def _attendance_unmarked(day, user=None):
    from apps.hr.attendance import unmarked_days
    from apps.hr.models import Employee

    yesterday = day - DAY
    return sum(len(unmarked_days(person, yesterday.replace(day=1), yesterday))
               for person in Employee.objects.filter(paid_by_attendance=True))


def _deliveries_unsigned(day, user=None):
    from apps.sales.models import Delivery

    return Delivery.objects.filter(posted=True, reverses__isnull=True, received_on__isnull=True,
                                   delivery_date__lte=day - UNSIGNED_FOR).count()


def _unbilled_freight(day, user=None):
    from apps.purchasing.freight import unbilled_freight

    return len(unbilled_freight())


def _invoices_overdue(day, user=None):
    from apps.sales.models import ar_aging

    return sum(bucket["count"] for key, bucket in ar_aging(as_of=day).items() if key != "current")


def _bills_overdue(day, user=None):
    from apps.purchasing.models import ap_aging

    return sum(bucket["count"] for key, bucket in ap_aging(as_of=day).items() if key != "current")


def _drafts(load, date_field):
    def count(day, user=None):
        return load().filter(posted=False, **{f"{date_field}__lte": day - DRAFT_FOR}).count()
    return count


def _invoices():
    from apps.sales.models import Invoice

    return Invoice.objects


def _bills():
    from apps.purchasing.models import Bill

    return Bill.objects


def _journals():
    from apps.accounting.models import JournalEntry

    return JournalEntry.objects


def _deliveries():
    from apps.sales.models import Delivery

    return Delivery.objects


def _stock_under_level(day, user=None):
    from apps.purchasing.models import reorder_suggestions

    return len(reorder_suggestions(on_date=day))


def _meters_unread(day, user=None):
    from apps.manufacturing.energy import summary

    yesterday = day - DAY
    return sum(1 for row in summary(yesterday, yesterday) if row["days_unread"])


def _follow_ups_due(day, user=None):
    from apps.sales.crm import follow_ups_due

    return follow_ups_due(user, day)


def _complaint_actions_overdue(day, user=None):
    from apps.manufacturing.complaints import overdue_actions

    return len(overdue_actions(on_date=day))


CHECKS = [
    Check("maintenance_due", "Maintenance due", ("manufacturing.view_maintenanceschedule",), "/plant/due",
          _maintenance_due),
    Check("calibration_due", "Instruments due for calibration within 30 days", ("quality.view_instrument",),
          "/quality/calibration-due", _calibration_due),
    Check("licences_due", "Licences to renew", ("core.view_licence",), "/settings/licences?renewed_by__isnull=true",
          _licences_due),
    Check("contractor_licences", "Contractor licences lapsing within 30 days", ("hr.view_labourcontractor",),
          "/payroll/labour-contractors", _contractor_licences),
    Check("msme_at_risk", "MSME bills past their 45 days, unpaid",
          ("purchasing.view_bill", "purchasing.view_purchaseorder"), "/accounts/msme-payments", _msme_at_risk),
    Check("attendance_unmarked", "Attendance days unmarked, day-rated", ("hr.view_attendanceday",),
          "/payroll/attendance-gaps", _attendance_unmarked),
    Check("deliveries_unsigned", "Deliveries a week out, not signed for", ("sales.view_delivery",),
          "/sales/unacknowledged", _deliveries_unsigned),
    Check("unbilled_freight", "Freight not yet billed",
          ("purchasing.view_bill", "sales.view_delivery", "purchasing.view_purchaseorder"),
          "/purchasing/unbilled-freight", _unbilled_freight),
    Check("invoices_overdue", "Invoices past due", ("sales.view_invoice",), "/sales/aging", _invoices_overdue),
    Check("bills_overdue", "Bills past due", ("purchasing.view_bill", "purchasing.view_purchaseorder"),
          "/purchasing/aging", _bills_overdue),
    Check("invoices_draft", "Invoices in draft over two days", ("sales.view_invoice",), "/sales/invoices?posted=false",
          _drafts(_invoices, "invoice_date")),
    Check("bills_draft", "Bills in draft over two days", ("purchasing.view_bill",), "/purchasing/bills?posted=false",
          _drafts(_bills, "bill_date")),
    Check("journals_draft", "Journal entries in draft over two days", ("accounting.view_journalentry",),
          "/accounts/journals?posted=false", _drafts(_journals, "date")),
    Check("deliveries_draft", "Deliveries in draft over two days", ("sales.view_delivery",),
          "/sales/deliveries?posted=false", _drafts(_deliveries, "delivery_date")),
    Check("stock_under_level", "Items under their reorder level", ("purchasing.view_purchaseorder",),
          "/purchasing/reorder", _stock_under_level),
    Check("meters_unread", "Meters unread yesterday", ("manufacturing.view_energymeter",), "/plant/energy",
          _meters_unread),
    Check("complaint_actions_overdue", "Complaint actions overdue", ("manufacturing.view_complaint",),
          "/quality/complaints", _complaint_actions_overdue),
    Check("follow_ups_due", "Follow-ups due", ("sales.view_activity",), "/sales/activities?done_on__isnull=true",
          _follow_ups_due),
]


def inbox(user, day=None):
    """[{key, label, count, href}] of what this login may act on, counting only what has something to count."""
    day = day or timezone.localdate()
    rows = []
    for check in CHECKS:
        if not all(user.has_perm(permission) for permission in check.permissions):
            continue
        count = check.count(day, user)
        if count:
            rows.append({"key": check.key, "label": check.label, "count": count, "href": check.href})
    return rows
