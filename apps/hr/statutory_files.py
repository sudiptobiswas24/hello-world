"""
The two files the payroll month ends with: EPFO's ECR (electronic
challan-cum-return) and ESIC's monthly contribution file, each from a
posted pay run, one line a member.

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
import io
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError

from .payroll import ComponentKind, PayRunStatus, Statutory

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
        kind = line.component.statutory
        if kind and line.kind != ComponentKind.EARNING:
            by_kind.setdefault(kind, []).append(line)
    return by_kind


def wage_base(slip, line):
    """What a percentage line was taken of, from the slip's own lines, capped as its component caps it."""
    component = line.component
    named = list(component.base_components.all())
    if named:
        wanted = {other.pk for other in named}
        base = sum((other.amount for other in slip.lines.all() if other.component_id in wanted), Decimal("0"))
    else:
        base = slip.taxable_gross()
    if component.base_ceiling is not None:
        base = min(base, component.base_ceiling)
    return base


def _posted(run):
    if run.status != PayRunStatus.POSTED:
        raise ValidationError("Only a posted run is filed; this one is "
                              f"{run.get_status_display().lower()}.")


def _slips(run):
    return run.payslips.select_related("employee__party").prefetch_related(
        "lines__component__base_components").order_by("employee__employee_number")


def ecr_rows(run):
    """[{uan, name, gross, epf_wages, eps_wages, edli_wages, epf, eps, epf_diff, ncp_days, refund}] for every member with a PF line."""
    _posted(run)
    rows, unnamed = [], []
    for slip in _slips(run):
        lines = _lines(slip)
        pf = lines.get(Statutory.PF)
        if not pf:
            continue
        if not slip.employee.uan:
            unnamed.append(slip.employee.employee_number)
            continue
        epf_wages = wage_base(slip, pf[0])
        eps_wages = min(epf_wages, EPS_CEILING)
        employer = sum((line.amount for line in lines.get(Statutory.PF_EMPLOYER, [])), Decimal("0"))
        named_eps = lines.get(Statutory.EPS)
        if named_eps:
            eps = sum((line.amount for line in named_eps), Decimal("0"))
            epf_diff = employer
        else:
            eps = (eps_wages * EPS_RATE / 100).quantize(RUPEE, rounding=ROUND_HALF_UP)
            epf_diff = employer - eps
        ncp = slip.unpaid_leave_days() + slip.absent_days()
        rows.append({
            "uan": slip.employee.uan, "name": slip.employee.party.name,
            "gross": _rupees(slip.gross()), "epf_wages": _rupees(epf_wages), "eps_wages": _rupees(eps_wages),
            "edli_wages": _rupees(eps_wages), "epf": _rupees(sum((line.amount for line in pf), Decimal("0"))),
            "eps": _rupees(eps), "epf_diff": _rupees(epf_diff), "ncp_days": _days(ncp), "refund": 0,
        })
    if unnamed:
        raise ValidationError(f"No UAN on {', '.join(unnamed)}: the ECR names a member by it. "
                              "Set it on the employee and ask again.")
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
    """[{ip, name, days, wages, reason, last_day}] for every insured person with an ESI line."""
    _posted(run)
    rows, unnamed = [], []
    for slip in _slips(run):
        lines = _lines(slip)
        esi = lines.get(Statutory.ESI)
        if not esi:
            continue
        if not slip.employee.esi_number:
            unnamed.append(slip.employee.employee_number)
            continue
        days = slip.days_employed() - slip.unpaid_leave_days() - slip.absent_days()
        left = slip.employee.termination_date
        rows.append({
            "ip": slip.employee.esi_number, "name": slip.employee.party.name,
            "days": max(_days(days), 0), "wages": _rupees(wage_base(slip, esi[0])),
            "reason": 0,
            "last_day": left.strftime("%d/%m/%Y") if left and run.period_start <= left <= run.period_end else "",
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
