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

from .payroll import PF_KINDS, WELFARE_FUND, ComponentBasis, PayComponent, Statutory
from .statutory import BONUS_CEILING, ENGINE_FIGURES, FigureKind, figure_standing, minimum_wage_on  # noqa: F401

ENGINES = ("TDS", "TCS", "PF", "ESI", "PT", "LWF", "Bonus", "Overtime", "Minimum wage", "Gratuity")


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
    """
    What is missing of each component's schedule in each state, named truly (O547): a state the component
    holds no slabs for has none (West Bengal's unconfirmed slabs were worded 'LWF MH (not confirmed)');
    a schedule that stands and waits for the Controller is not confirmed.
    """
    missing = []
    for component in components:
        for state in states:
            where = f" {state}" if state else ""
            held = component.has_no_schedule_for(state)
            if held and state:
                # What a run does meanwhile, said as it is: the welfare fund is not taken there (O547);
                # professional tax on the pay of someone there refuses the run (review L4).
                meanwhile = (f"so nothing is taken from people who work in {state}"
                             if component.statutory in WELFARE_FUND
                             else f"and a pay run with it on the pay of someone who works in {state} is refused")
                missing.append(
                    f"{component.code}{where} (none: its schedule is {held}, {meanwhile}; add {state}'s slabs, "
                    f"or a slab of nothing for {state} if it levies none)")
                continue
            if held:
                missing.append(f"{component.code} (none: its schedule is {held}, and an active employee's "
                               "state is not said)")
                continue
            try:
                if not component.schedule_on(day, state):
                    missing.append(f"{component.code}{where} (none)")
            except ValidationError:
                missing.append(f"{component.code}{where} (not confirmed)")
    return missing


def _levy(engine, kinds, levied_in, marked, day, states):
    """
    A state levy is used where the plant's active employees work in a state
    its file (statutory/<levied_in>.csv) names, whatever the components say:
    a component unmarked is not a levy gone (review p03). Each such state needs
    a component marked as each share with a confirmed schedule standing today;
    and a marked component answers for every state the plant works in, the
    ones its file does not name too (O547).
    """
    from apps.accounting.statutory_load import rows

    levying = {row["state"] for row in rows(levied_in)}
    where = [state for state in states if state in levying]
    missing = []
    for kind in kinds:
        components = marked(kind)
        if not components and where:
            missing.append(f"no active pay component marked '{Statutory(kind).label}' for {', '.join(where)}")
        missing += _schedules(components, day, states)
    return engine, bool(where or marked(*kinds)), missing


def _not_given(components, day):
    """Who is paid a component's wages and does not have the component: an accrual nobody remembered to give."""
    from .bonus import people_without

    missing = []
    for component in components:
        people = people_without(component, day)
        if people:
            missing.append(f"{component.code} on the pay of {', '.join(p.employee_number for p in people)} "
                           "(not given: python manage.py setup_payroll gives it)")
    return missing


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
        # Each engine's line asks every figure its calculation reads (statutory.ENGINE_FIGURES).
        ("PF", bool(marked(*PF_KINDS)), _figures(ENGINE_FIGURES["PF"], day, states)),
        ("ESI", bool(marked(Statutory.ESI, Statutory.ESI_EMPLOYER)), _figures(ENGINE_FIGURES["ESI"], day, states)),
        _levy("PT", (Statutory.PT,), "professional_tax", marked, day, states),
        _levy("LWF", (Statutory.LWF, Statutory.LWF_EMPLOYER), "labour_welfare_fund", marked, day, states),
        ("Bonus", bool(bonus), _figures(ENGINE_FIGURES["Bonus"], day, states) + _not_given(bonus, day)),
        ("Overtime", active.filter(basis=ComponentBasis.OVERTIME_ORDINARY).exists(),
         _figures(ENGINE_FIGURES["Overtime"], day, states)),
    ]
    wages = []
    for person in employees:
        try:
            minimum_wage_on(person, day)
        except ValidationError:
            wages.append(person.employee_number)
    found.append(("Minimum wage", bool(employees), wages))
    from .gratuity import PROVISION_CODE

    provision = list(active.filter(code=PROVISION_CODE))
    found.append(("Gratuity", bool(provision), _not_given(provision, day)))
    return found
