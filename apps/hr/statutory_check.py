"""
Whether every statutory engine the plant uses has a confirmed figure
covering a day: one line per engine for go_live_check. An engine is used
when something would post through it (an active TDS or TCS section, a pay
component marked as the levy, a bonus component on the bonus rule, an
active employee for the minimum wage). Each line names what is missing.
The engines' own readers decide (rate_standing, figure_standing,
PayComponent.schedule_on, minimum_wage_on), so the check cannot pass on a
figure the run would refuse.
"""

from django.core.exceptions import ValidationError

from apps.core.models import to_date

from .payroll import PF_KINDS, PayComponent, Statutory
from .statutory import BONUS_CEILING, FigureKind, figure_standing, minimum_wage_on

ENGINES = ("TDS", "TCS", "PF", "ESI", "PT", "LWF", "Bonus", "Minimum wage")


def _figures(kinds, day, states):
    missing = []
    for kind in kinds:
        for state in states:
            row = figure_standing(kind, day, state)
            if row is None or row.confirmed_on is None:
                where = row.state if row is not None else state
                where = f" in {where}" if where else ""
                missing.append(f"{FigureKind(kind).label}{where}"
                               + (" (loaded, not confirmed)" if row is not None else " (none)"))
    return sorted(set(missing))


def _rates(model, day):
    missing = []
    for section in model.objects.filter(is_active=True).order_by("code"):
        try:
            section.rate_on(day)
        except ValidationError:
            missing.append(section.code)
    return missing


def _schedules(components, day, states):
    missing = []
    for component in components:
        for state in states:
            try:
                if not component.schedule_on(day, state):
                    missing.append(f"{component.code}{' ' + state if state else ''} (none)")
            except ValidationError:
                missing.append(f"{component.code}{' ' + state if state else ''} (not confirmed)")
    return missing


def _levy(engine, kinds, levied_in, marked, day, states):
    """
    A state levy is used where the plant's active employees work in a state
    its file (statutory/<levied_in>.csv) names, whatever the components say:
    a component unmarked is not a levy gone (review p03). Each such state needs
    a component marked as each share with a confirmed schedule standing today.
    """
    from apps.accounting.statutory_load import rows

    levying = {row["state"] for row in rows(levied_in)}
    where = [state for state in states if state in levying]
    missing = []
    for kind in kinds:
        components = marked(kind)
        if not components and where:
            missing.append(f"no active pay component marked '{Statutory(kind).label}' for {', '.join(where)}")
        missing += _schedules(components, day, where or states)
    return engine, bool(where or marked(*kinds)), missing


def statutory_gaps(day):
    """[(engine, used, missing)] for every engine, in ENGINES order."""
    from apps.accounting.models import TcsSection, TdsSection

    from .models import Employee

    day = to_date(day)
    employees = list(Employee.objects.filter(employment_status="active").select_related("pay_details"))
    states = sorted({person.work_state for person in employees}) or [""]
    active = PayComponent.objects.filter(is_active=True)
    marked = lambda *kinds: list(active.filter(statutory__in=kinds).order_by("code"))  # noqa: E731
    bonus = list(active.filter(ceiling_figure=BONUS_CEILING))
    found = [
        ("TDS", TdsSection.objects.filter(is_active=True).exists(), _rates(TdsSection, day)),
        ("TCS", TcsSection.objects.filter(is_active=True).exists(), _rates(TcsSection, day)),
        ("PF", bool(marked(*PF_KINDS)),
         _figures((FigureKind.EPF_WAGE_CEILING, FigureKind.EPS_WAGE_CEILING), day, states)),
        ("ESI", bool(marked(Statutory.ESI, Statutory.ESI_EMPLOYER)),
         _figures((FigureKind.ESI_EMPLOYEE_RATE, FigureKind.ESI_EMPLOYER_RATE, FigureKind.ESI_WAGE_CEILING),
                  day, states)),
        _levy("PT", (Statutory.PT,), "professional_tax", marked, day, states),
        _levy("LWF", (Statutory.LWF, Statutory.LWF_EMPLOYER), "labour_welfare_fund", marked, day, states),
        ("Bonus", bool(bonus), _figures((FigureKind.BONUS_FLOOR, FigureKind.BONUS_ELIGIBILITY), day, states)),
    ]
    wages = []
    for person in employees:
        try:
            minimum_wage_on(person, day)
        except ValidationError:
            wages.append(person.employee_number)
    found.append(("Minimum wage", bool(employees), wages))
    return found
