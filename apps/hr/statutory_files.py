"""
The two files the payroll month ends with: EPFO's ECR (electronic
challan-cum-return) and ESIC's monthly contribution file, each for the
month of a posted pay run, one line a member a month over all its posted runs.

Everything in them is read off the posted payslip lines, never worked
out again: a component says which statutory line it is (`statutory`),
the employee carries the number the portal knows them by, and a wage
base is the sum of the slip's lines the component is taken of, capped as
the component caps it. The pension (EPS) share is 8.33% of pension
wages, named on its own component where the plant keeps one and taken
out of the employer's provident-fund share where it does not; the
employer's EPF share is what is left. A member the portal cannot name
(no UAN, no IP number) stops the file rather than going in wrong.

Formats: the ECR is `#~#`-delimited text, eleven fields a line (UAN,
member name, gross wages, EPF wages, EPS wages, EDLI wages, EPF
contribution, EPS contribution, EPF-EPS difference, NCP days, refund of
advances), amounts in whole rupees. The ESI file is the portal's upload
template's six columns, as CSV to paste into it.
"""

import csv
import datetime
import io
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError

from apps.core.models import to_date

from .payroll import ComponentKind, PayRunStatus, Statutory
from .statutory import pension_stops

EPS_CEILING = Decimal("15000")
EPS_RATE = Decimal("8.33")
RUPEE = Decimal("1")


def period_code(run):
    return f"{run.period_end:%Y%m}"


def _rupees(amount):
    return int(Decimal(amount).quantize(RUPEE, rounding=ROUND_HALF_UP))


def _days(amount):
    return int(Decimal(amount).quantize(RUPEE, rounding=ROUND_HALF_UP))


def _lines(slip):
    """The slip's lines by their statutory kind; an earning's kind is ignored."""
    by_kind = {}
    for line in slip.lines.all():
        # The marker as it stood for the month (R7); a slip from before it was kept reads it now.
        kind = line.kept_statutory if slip.kept_paid_days is not None else line.component.statutory
        if kind and line.kind != ComponentKind.EARNING:
            by_kind.setdefault(kind, []).append(line)
    return by_kind


def wage_base(slip, line):
    """What a percentage line was taken of, from the slip's own lines, capped as its component caps it."""
    base, ceiling = _base_and_ceiling(slip, line)
    return base if ceiling is None else min(base, ceiling)


def _base_and_ceiling(slip, line):
    """(the slip's base for `line`, uncapped; the ceiling its component capped it at, or None)."""
    component = line.component
    # The list as it stood for the month (R7); a slip from before it was kept reads it now.
    named = (line.kept_base if slip.kept_paid_days is not None and line.kept_base is not None
             else [other.pk for other in component.base_components.all()])
    if named:
        wanted = set(named)
        # Earnings only (R3): a leaver's final slip carries what is taken back
        # on the same component as a deduction, and counted, the ECR filed PF
        # wages of 675 earned plus 256.50 taken back. Not reduced by the
        # recovery (the owner's decision), and not raised by it either.
        base = sum((other.amount for other in slip.lines.all()
                    if other.component_id in wanted and other.kind == ComponentKind.EARNING), Decimal("0"))
    else:
        base = slip.taxable_gross()
    # The ceiling as it stood when the slip was worked out (O6); a slip from
    # before it was kept reads the component's.
    ceiling = line.base_ceiling if slip.kept_paid_days is not None else component.base_ceiling
    return base, ceiling


def pension_ceiling(line):
    """The EPS wage ceiling the line was worked out at (O222); a line from before it was kept, the old constant."""
    term = (line.kept_terms or {}).get("eps_ceiling")
    return Decimal(term[2]) if term else EPS_CEILING


def born_as_kept(slip, line):
    """
    The date of birth the run read for the slip (kept on its PF line). A run that read none, or a slip
    from before it was kept, reads it now: entered since, it is a fact the run lacked, not a change.
    """
    terms = line.kept_terms or {}
    if terms.get("born"):
        return to_date(terms["born"])
    details = getattr(slip.employee, "pay_details", None)
    return details.date_of_birth if details is not None else None


def _posted(run):
    if run.status != PayRunStatus.POSTED:
        raise ValidationError("Only a posted run is filed; this one is "
                              f"{run.get_status_display().lower()}.")


def _slips(run):
    return run.payslips.select_related("employee__party").prefetch_related(
        "lines__component__base_components").order_by("employee__employee_number")


def _month_members(run, kind):
    """
    {employee: [(slip, lines by kind)]} for every member with a `kind` line on a posted run of `run`'s month,
    in employee-number order: each file is one row a member a month (O301, decided), whatever number of runs
    paid the month. Two fortnights of June filed a member twice, each at its own run's wages.
    """
    members = {}
    for month_run in _month_runs(run.period_end).order_by("period_end", "pk"):
        for slip in _slips(month_run):
            members.setdefault(slip.employee, []).append((slip, _lines(slip)))
    # Every run of the month counts in the member's row, also one that took nothing (review 2): the ceiling
    # reached in the first fortnight, the second has no PF line, and its gross and unpaid days were left out.
    return dict(sorted(((employee, slips) for employee, slips in members.items()
                        if any(lines.get(kind) for _slip, lines in slips)),
                       key=lambda item: item[0].employee_number))


def _last_line(slips, kind):
    return [lines[kind][0] for _slip, lines in slips if lines.get(kind)][-1]


def _month_base(slips, kind):
    """
    The month's base for `kind`: each slip's uncapped, summed, capped once at the last ceiling. A slip with no
    `kind` line is taken of what the member's last line of it was taken of.
    """
    base, reference = Decimal("0"), _last_line(slips, kind)
    for slip, lines in slips:
        here, _ceiling = _base_and_ceiling(slip, (lines.get(kind) or [reference])[0])
        base += here
    ceiling = _base_and_ceiling(reference.payslip, reference)[1]
    return base if ceiling is None else min(base, ceiling)


def _total(slips, kind):
    return sum((line.amount for _slip, lines in slips for line in lines.get(kind, [])), Decimal("0"))


def ecr_rows(run):
    """[{uan, name, gross, epf_wages, eps_wages, edli_wages, epf, eps, epf_diff, ncp_days, refund}] a member a month."""
    _posted(run)
    rows, unnamed, undated = [], [], []
    for employee, slips in _month_members(run, Statutory.PF).items():
        if not employee.uan:
            unnamed.append(employee.employee_number)
            continue
        pf = [_last_line(slips, Statutory.PF)]
        last = pf[0].payslip
        epf_wages = _month_base(slips, Statutory.PF)
        capped = min(epf_wages, pension_ceiling(pf[0]))
        born = born_as_kept(last, pf[0])
        if born is None:
            undated.append(employee.employee_number)
            continue
        # From the month after the member turns 58, no pension: the whole employer share is EPF's.
        eps_wages = Decimal("0") if pension_stops(born, last.run.period_end) else capped
        employer = _total(slips, Statutory.PF_EMPLOYER)
        if any(lines.get(Statutory.EPS) for _slip, lines in slips):
            eps = _total(slips, Statutory.EPS)
            epf_diff = employer
        else:
            eps = (eps_wages * EPS_RATE / 100).quantize(RUPEE, rounding=ROUND_HALF_UP)
            epf_diff = employer - eps
        ncp = sum((slip.kept_days()[1] for slip, _lines in slips), Decimal("0"))
        rows.append({
            "uan": employee.uan, "name": employee.party.name,
            "gross": _rupees(sum((slip.gross() for slip, _lines in slips), Decimal("0"))),
            "epf_wages": _rupees(epf_wages), "eps_wages": _rupees(eps_wages),
            "edli_wages": _rupees(capped), "epf": _rupees(_total(slips, Statutory.PF)),
            "eps": _rupees(eps), "epf_diff": _rupees(epf_diff), "ncp_days": _days(ncp), "refund": 0,
        })
    if unnamed:
        raise ValidationError(f"No UAN on {', '.join(unnamed)}: the ECR names a member by it. "
                              "Set it on the employee and ask again.")
    if undated:
        raise ValidationError(f"No date of birth on {', '.join(undated)}: the pension share stops the month after "
                              "a member turns 58, and the ECR cannot tell. Enter it in their pay details.")
    return rows


def ecr_text(run):
    return "".join(
        "#~#".join(str(row[key]) for key in ("uan", "name", "gross", "epf_wages", "eps_wages", "edli_wages",
                                              "epf", "eps", "epf_diff", "ncp_days", "refund")) + "\r\n"
        for row in ecr_rows(run))


ESI_HEADINGS = [
    "IP Number (10 Digits)",
    "IP Name (Only alphabets and space)",
    "No of Days for which wages paid/payable during the month",
    "Total Monthly Wages",
    "Reason Code for Zero workings days (numeric only; provide 0 for all other reasons- Click on the link for reference)",
    "Last Working Day (Format DD/MM/YYYY) (If relieved/left service)",
]


def esi_rows(run):
    """[{ip, name, days, wages, reason, last_day}] an insured person a month, over the month's posted runs."""
    _posted(run)
    rows, unnamed = [], []
    first = run.period_end.replace(day=1)
    following = (first + datetime.timedelta(days=32)).replace(day=1)
    for employee, slips in _month_members(run, Statutory.ESI).items():
        if not employee.esi_number:
            unnamed.append(employee.employee_number)
            continue
        days = sum((slip.kept_days()[0] for slip, _lines in slips), Decimal("0"))
        left = employee.termination_date
        rows.append({
            "ip": employee.esi_number, "name": employee.party.name,
            "days": max(_days(days), 0), "wages": _rupees(_month_base(slips, Statutory.ESI)),
            "reason": 0,
            "last_day": left.strftime("%d/%m/%Y") if left and first <= left < following else "",
        })
    if unnamed:
        raise ValidationError(f"No ESI number on {', '.join(unnamed)}: the contribution file names a "
                              "person by it. Set it on the employee and ask again.")
    return rows


def esi_csv(run):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(ESI_HEADINGS)
    for row in esi_rows(run):
        writer.writerow([row["ip"], row["name"], row["days"], row["wages"], row["reason"], row["last_day"]])
    return out.getvalue()


def _schedule(line, key):
    term = (line.kept_terms or {}).get(key) or [None, None, ""]
    return {"state": term[2] or "", "from": term[1] or ""}


def _month_runs(month):
    """Posted runs whose period ends in `month` (a date in it)."""
    from .payroll import PayRun

    first = month.replace(day=1)
    following = (first.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    return PayRun.objects.filter(status=PayRunStatus.POSTED, period_end__gte=first, period_end__lt=following)


def pt_register(month):
    """
    Professional tax for `month`: one row a slip with a PT line, from the
    posted runs, at the wages, amount and schedule (state, from) the slip
    kept; never today's slabs.
    """
    rows = []
    for run in _month_runs(month).order_by("period_end", "pk"):
        for slip in _slips(run):
            lines = _lines(slip).get(Statutory.PT)
            if not lines:
                continue
            rows.append({"run": run.number, "employee": slip.employee.employee_number,
                         "name": slip.employee.party.name,
                         # The month's wages the band was read at: this run's, and what runs before it of the
                         # month paid on (kept on the line, review RP5).
                         "wages": wage_base(slip, lines[0]) + Decimal(
                             ((lines[0].kept_terms or {}).get("month_before") or ["0"])[0]),
                         "tax": sum((line.amount for line in lines), Decimal("0")),
                         **_schedule(lines[0], "slab")})
    return rows


def lwf_register(month):
    """The Labour Welfare Fund for `month`: each slip's employee and employer shares as kept."""
    rows = []
    for run in _month_runs(month).order_by("period_end", "pk"):
        for slip in _slips(run):
            lines = _lines(slip)
            mine, theirs = lines.get(Statutory.LWF, []), lines.get(Statutory.LWF_EMPLOYER, [])
            if not mine and not theirs:
                continue
            rows.append({"run": run.number, "employee": slip.employee.employee_number,
                         "name": slip.employee.party.name,
                         "employee_share": sum((line.amount for line in mine), Decimal("0")),
                         "employer_share": sum((line.amount for line in theirs), Decimal("0")),
                         **_schedule((mine or theirs)[0], "slab")})
    return rows
