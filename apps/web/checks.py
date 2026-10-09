"""
What fell due overnight, for whoever it is due to. Each check is one
question the office used to have to remember to ask: a count, the screen
that lists exactly those, and the permissions that read it. The home
page shows a login its own rows; `morning_checks` mails them.

Nothing here runs by itself: cron runs the command each morning, and the
page asks when it opens. A check that has nothing to say is left out,
so an empty inbox is an empty inbox.

All but one of the questions are the plant's and have one answer a day,
whoever asks; `inbox()` takes a dict to remember them in, so the command
counts each once for every login. The one that is a person's own (their
follow-ups) is marked `per_login` and counted for each.

A count over rows that name a customer is `scoped`: a login the rep scope
limits gets its own count, of what it may see, and not the plant's. The
inbox told a rep how many invoices were overdue across every rep's
customers.

The last group is not what fell due but what was left incomplete and
carries forward if nobody says so: a customer with no GST standing, an
item with no HSN, a PF member with no UAN, money received and applied to
nothing, a bank line nobody explained, a month whose depreciation or
wages were never posted, an invoice the portal never saw; and the books
disagreeing with themselves (health.py), which is the one that matters
most and shows least.
"""

import datetime
from dataclasses import dataclass
from typing import Callable

from django.apps import apps
from django.utils import timezone

DAY = datetime.timedelta(days=1)
DRAFT_FOR = 2 * DAY
UNSIGNED_FOR = 7 * DAY
UNAPPLIED_FOR = 7 * DAY  # money received that nobody has matched to an invoice in a week
UNEXPLAINED_FOR = 7 * DAY  # a bank line without its payment or journal in a week
WAGES_BY = 7  # the day of the month by which last month's wages are posted
REGISTERED = ("regular", "composition", "sez")  # GST standings that come with a GSTIN


@dataclass(frozen=True)
class Check:
    key: str
    label: str
    permissions: tuple
    href: str
    count: Callable  # (day) -> int, or (user, day) -> int where per_login, or (day, user) where scoped
    per_login: bool = False
    scoped: bool = False  # its rows name a customer: counted again for a login the rep scope limits


def _visible(queryset, user, path):
    """Rows of `queryset` whose party (at `path`) `user` may see; all of them for the plant's count."""
    if user is None:
        return queryset
    from apps.core.scoping import scoped

    return scoped(queryset, user, path)


def _maintenance_due(day):
    from apps.manufacturing.maintenance import due_now

    return len(due_now(as_of=day))


def _alerts_open(day):
    from apps.manufacturing.alerts import AlertStatus, QualityAlert

    return QualityAlert.objects.filter(status=AlertStatus.OPEN, raised_on__lte=day - datetime.timedelta(days=14)).count()


def _calibration_due(day):
    from apps.quality.calibration import due

    return len(due(within_days=30, on_date=day))


def _licences_due(day):
    from apps.core.licences import licences_due

    return len(licences_due(day))


def _contractor_licences(day):
    from apps.hr.contract_labour import licences_due

    return len(licences_due(within_days=30, on_date=day))


def _msme_at_risk(day):
    from apps.purchasing.msme import msme_bills

    return sum(1 for row in msme_bills(day - 365 * DAY, day, as_of=day) if row["at_risk"] > 0)


def _attendance_unmarked(day):
    from apps.hr.attendance import unmarked_report

    yesterday = day - DAY
    return sum(len(row["days"]) for row in unmarked_report(yesterday.replace(day=1), yesterday))


def _deliveries_unsigned(day, user=None):
    from apps.sales.models import Delivery

    return _visible(Delivery.objects.filter(posted=True, reverses__isnull=True, received_on__isnull=True,
                                            delivery_date__lte=day - UNSIGNED_FOR), user, "sales_order__customer").count()


def _unbilled_freight(day):
    from apps.purchasing.freight import unbilled_freight

    return len(unbilled_freight())


def _invoices_overdue(day, user=None):
    from apps.sales.models import Invoice, ar_aging

    invoices = _visible(Invoice.objects.all(), user, "customer")
    return sum(bucket["count"] for key, bucket in ar_aging(as_of=day, invoices=invoices).items() if key != "current")


def _bills_overdue(day):
    from apps.purchasing.models import ap_aging

    return sum(bucket["count"] for key, bucket in ap_aging(as_of=day).items() if key != "current")


def _drafts(model, date_field, party=None):
    def count(day, user=None):
        rows = apps.get_model(model).objects.filter(posted=False, **{f"{date_field}__lte": day - DRAFT_FOR})
        return (_visible(rows, user, party) if party else rows).count()
    return count


def _stock_under_level(day):
    from apps.purchasing.models import reorder_suggestions

    return len(reorder_suggestions(on_date=day))


def _meters_unread(day):
    from apps.manufacturing.energy import summary

    yesterday = day - DAY
    return sum(1 for row in summary(yesterday, yesterday) if row["days_unread"])


def _follow_ups_due(user, day):
    from apps.sales.crm import follow_ups_due

    return follow_ups_due(user, day)


def _complaint_actions_overdue(day):
    from apps.manufacturing.complaints import overdue_actions

    return len(overdue_actions(on_date=day))


def _recurring_journals_due(day):
    from apps.accounting.recurring import RecurringJournal

    return sum(1 for schedule in RecurringJournal.objects.filter(is_active=True) if schedule.is_due(day))


def _recurring_invoices_due(day, user=None):
    from apps.sales.models import RecurringInvoice

    return sum(1 for schedule in _visible(RecurringInvoice.objects.filter(is_active=True), user, "customer")
               if schedule.next_run_date and schedule.next_run_date <= day and not schedule.has_finished())


def _gst_in_use():
    from apps.accounting.gst import GstSettings

    return GstSettings.active() is not None


def _customers_without_gst(day, user=None):
    """Customers whose GST standing is unknown, or registered with no GSTIN: the invoice will be wrong."""
    if not _gst_in_use():
        return 0
    from apps.core.models import Party

    return customers_without_gst(_visible(Party.objects.all(), user, None)).count()


def customers_without_gst(parties):
    from django.db.models import Q

    from apps.core.models import PartyRole

    return parties.filter(is_active=True, role_assignments__role=PartyRole.CUSTOMER).filter(
        Q(tax_profile__isnull=True) | Q(tax_profile__gst_registration="")
        | Q(tax_profile__gst_registration__in=REGISTERED, tax_profile__gstin="")).distinct()


def _items_without_hsn(day):
    """Every e-way bill, job-work challan and GSTR-1 line names an HSN; an item without one stops them."""
    if not _gst_in_use():
        return 0
    from apps.inventory.models import Item

    return Item.objects.filter(is_active=True, hsn_code="").count()


def members_without(employees, field, kinds, day):
    """Active employees on a statutory component of `kinds` today with `field` blank."""
    from django.db.models import Q

    from apps.hr.models import EmploymentStatus

    return employees.filter(
        Q(employment_status=EmploymentStatus.ACTIVE, **{field: ""})
        & Q(compensation__component__statutory__in=kinds, compensation__effective_from__lte=day)
        & (Q(compensation__effective_to__isnull=True) | Q(compensation__effective_to__gte=day))).distinct()


def _pf_without_uan(day):
    from apps.hr.models import Employee
    from apps.hr.payroll import Statutory

    return members_without(Employee.objects.all(), "uan", (Statutory.PF, Statutory.PF_EMPLOYER), day).count()


def _esi_without_number(day):
    from apps.hr.models import Employee
    from apps.hr.payroll import Statutory

    return members_without(Employee.objects.all(), "esi_number", (Statutory.ESI, Statutory.ESI_EMPLOYER), day).count()


def _unapplied(direction):
    def count(day, user=None):
        from apps.accounting.models import Payment
        from apps.accounting.settlement import unapplied

        rows = unapplied(_visible(Payment.objects.filter(posted=True, voided_entry__isnull=True, direction=direction,
                                                         payment_date__lte=day - UNAPPLIED_FOR), user, "party"))
        return sum(1 for payment in rows.select_related("journal_entry").prefetch_related("journal_entry__reversed_by")
                   if not payment.is_voided())
    return count


def _bank_lines_unexplained(day):
    from apps.accounting.models import BankStatementLine

    return BankStatementLine.objects.filter(statement__closed=False, date__lte=day - UNEXPLAINED_FOR,
                                            payment__isnull=True, journal_entry__isnull=True).count()


def _last_month(day):
    end = day.replace(day=1) - DAY
    return end.replace(day=1), end


def _depreciation_not_run(day):
    """Assets in service with last month (or earlier) still to be charged."""
    from apps.assets.models import AssetStatus, FixedAsset

    _, end = _last_month(day)
    return sum(1 for asset in FixedAsset.objects.filter(status=AssetStatus.IN_SERVICE)
               .prefetch_related("depreciation_entries") if asset.periods_due(end))


def _wages_not_posted(day):
    """Past the day wages are paid by, with people on the payroll and no posted run for last month."""
    from apps.hr.models import Employee, EmploymentStatus
    from apps.hr.payroll import PayRun, PayRunStatus

    if day.day <= WAGES_BY:
        return 0
    start, end = _last_month(day)
    on_payroll = Employee.objects.filter(employment_status=EmploymentStatus.ACTIVE, compensation__effective_from__lte=end)
    if not on_payroll.exists():
        return 0
    covered = PayRun.objects.filter(status=PayRunStatus.POSTED, period_start__lte=end, period_end__gte=start)
    return 0 if covered.exists() else 1


def invoices_without_irn(invoices):
    """Posted invoices e-invoicing covers (gst.einvoice.needs_irn) that the portal has not registered."""
    from django.db.models import Q

    from apps.accounting.gst import GstSettings

    registration = GstSettings.active()
    if registration is None or registration.einvoicing_from is None:
        return invoices.none()
    return (invoices.filter(posted=True, invoice_date__gte=registration.einvoicing_from, is_down_payment=False,
                            is_opening_balance=False)
            .filter(Q(party_registration="overseas") | ~Q(party_gstin=""))
            .exclude(credits__is_down_payment=True)
            .exclude(credits__is_opening_balance=True, corrects_old_supply=False)
            .filter(Q(einvoice__isnull=True) | Q(einvoice__irn="")))


def _invoices_without_irn(day, user=None):
    from apps.sales.models import Invoice

    return invoices_without_irn(_visible(Invoice.objects.all(), user, "customer")).count()


def _books_disagree(day):
    from .health import integrity

    return sum(1 for finding in integrity(day)["findings"] if finding["ok"] is False)


def _backup_stale(day):
    from .health import backups

    kept = backups()
    return 1 if kept["configured"] and kept["stale"] else 0


CHECKS = [
    Check("maintenance_due", "Maintenance due", ("manufacturing.view_maintenanceschedule",), "/plant/due",
          _maintenance_due),
    Check("alerts_open", "Quality alerts open a fortnight or more", ("manufacturing.view_qualityalert",),
          "/quality/alerts?status=open", _alerts_open),
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
          "/sales/unacknowledged", _deliveries_unsigned, scoped=True),
    Check("unbilled_freight", "Freight not yet billed",
          ("purchasing.view_bill", "sales.view_delivery", "purchasing.view_purchaseorder"),
          "/purchasing/unbilled-freight", _unbilled_freight),
    Check("invoices_overdue", "Invoices past due", ("sales.view_invoice",), "/sales/aging", _invoices_overdue,
          scoped=True),
    Check("bills_overdue", "Bills past due", ("purchasing.view_bill", "purchasing.view_purchaseorder"),
          "/purchasing/aging", _bills_overdue),
    Check("invoices_draft", "Invoices in draft over two days", ("sales.view_invoice",), "/sales/invoices?posted=false",
          _drafts("sales.Invoice", "invoice_date", "customer"), scoped=True),
    Check("bills_draft", "Bills in draft over two days", ("purchasing.view_bill",), "/purchasing/bills?posted=false",
          _drafts("purchasing.Bill", "bill_date")),
    Check("journals_draft", "Journal entries in draft over two days", ("accounting.view_journalentry",),
          "/accounts/journals?posted=false", _drafts("accounting.JournalEntry", "date")),
    Check("deliveries_draft", "Deliveries in draft over two days", ("sales.view_delivery",),
          "/sales/deliveries?posted=false", _drafts("sales.Delivery", "delivery_date", "sales_order__customer"),
          scoped=True),
    Check("stock_under_level", "Items under their reorder level", ("purchasing.view_purchaseorder",),
          "/purchasing/reorder", _stock_under_level),
    Check("meters_unread", "Meters unread yesterday", ("manufacturing.view_energymeter",), "/plant/energy",
          _meters_unread),
    Check("complaint_actions_overdue", "Complaint actions overdue", ("manufacturing.view_complaint",),
          "/quality/complaints", _complaint_actions_overdue),
    Check("recurring_journals_due", "Recurring journal entries due", ("accounting.view_recurringjournal",),
          "/accounts/recurring-journals?is_active=true", _recurring_journals_due),
    Check("recurring_invoices_due", "Recurring invoices due", ("sales.view_recurringinvoice",),
          "/sales/recurring?is_active=true", _recurring_invoices_due, scoped=True),
    Check("follow_ups_due", "Follow-ups due", ("sales.view_activity",), "/sales/activities?done_on__isnull=true",
          _follow_ups_due, per_login=True),
    # Left incomplete, and carried forward until said.
    Check("customers_without_gst", "Customers with no GST standing or GSTIN", ("accounting.view_partytaxprofile",),
          "/sales/customers?gst=unknown", _customers_without_gst, scoped=True),
    Check("items_without_hsn", "Items with no HSN code", ("inventory.view_item",),
          "/stores/items?without_hsn=true&is_active=true", _items_without_hsn),
    Check("pf_without_uan", "PF members with no UAN", ("hr.view_employee",), "/payroll/employees?missing=uan",
          _pf_without_uan),
    Check("esi_without_number", "ESI members with no insurance number", ("hr.view_employee",),
          "/payroll/employees?missing=esi_number", _esi_without_number),
    Check("receipts_unapplied", "Money received a week ago, applied to nothing", ("accounting.view_payment",),
          "/sales/receipts?unapplied=true", _unapplied("receipt"), scoped=True),
    Check("payments_unapplied", "Money paid a week ago, applied to nothing", ("accounting.view_payment",),
          "/purchasing/payments?unapplied=true", _unapplied("disbursement")),
    Check("bank_lines_unexplained", "Bank lines a week old, unexplained", ("accounting.view_bankstatement",),
          "/accounts/bank-statements?closed=false", _bank_lines_unexplained),
    Check("depreciation_not_run", "Assets with last month's depreciation not charged", ("assets.view_fixedasset",),
          "/accounts/assets?status=in_service", _depreciation_not_run),
    Check("wages_not_posted", "Last month's wages not posted", ("hr.view_payrun",), "/payroll/pay-runs",
          _wages_not_posted),
    Check("invoices_without_irn", "Invoices the e-invoice portal has not registered", ("gst.view_einvoice",),
          "/sales/invoices?without_irn=true", _invoices_without_irn, scoped=True),
    # The books against themselves, and the machine.
    Check("books_disagree", "Books that do not agree with themselves", ("core.check_health",), "/settings/health",
          _books_disagree),
    Check("backup_stale", "No backup in the last day", ("core.check_health",), "/settings/health", _backup_stale),
]


def inbox(user, day=None, counted=None, everything=False):
    """
    [{key, label, count, href}] of what this login may act on, counting
    only what has something to count (every check it may read, with
    `everything`). `counted` ({key: count}) remembers the plant-wide
    answers between logins asked on the same day.
    """
    from apps.core.scoping import visible_parties

    day = day or timezone.localdate()
    counted = {} if counted is None else counted
    limited = visible_parties(user) is not None
    rows = []
    for check in CHECKS:
        if not all(user.has_perm(permission) for permission in check.permissions):
            continue
        if check.per_login:
            count = check.count(user, day)
        elif check.scoped and limited:
            count = check.count(day, user)
        else:
            if check.key not in counted:
                counted[check.key] = check.count(day)
            count = counted[check.key]
        if count or everything:
            rows.append({"key": check.key, "label": check.label, "count": count, "href": check.href})
    return rows
