"""
The owner's morning question: what did each section make yesterday, at
what wastage, and at how many units a kilogramme.

One row a section (a work centre), and a row a unit where a section
booked output in more than one. Kilogrammes are what the item converts
to (a tape run is already in them; a sack converts through its
specification) or, failing that, what was weighed: the fabric rolls and
tape doffs off the entry. A section whose output nothing converts shows
its unit and no kilogrammes, so its kWh a kilogramme is blank rather
than wrong. The kWh is the section's meters' whole draw for the day,
idle included, because that is what the bill charges.
"""

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum

from apps.core.models import UnitOfMeasure, UnitOfMeasureCategory

from .energy import EnergyMeter, summary
from .orders import ProductionEntry, WorkCentre
from .rolls import FabricRoll
from .station_tape import TapeDoff

ZERO = Decimal("0")
PERCENT = Decimal("0.01")
THOUSANDTH = Decimal("0.001")


def kilogramme():
    """The plant's kilogramme: the weight unit coded kg, if it keeps one."""
    return UnitOfMeasure.objects.filter(code__iexact="kg", category=UnitOfMeasureCategory.WEIGHT).first()


def weighed(entries):
    """{entry id: kg} from what was weighed off each entry: its rolls, or its doffs where no roll was."""
    rolls = dict(FabricRoll.objects.filter(entry__in=entries).values_list("entry").annotate(kg=Sum("net_weight_kg")))
    doffs = dict(TapeDoff.objects.filter(entry__in=entries).values_list("entry").annotate(kg=Sum("net_kg")))
    return {**doffs, **rolls}


def kilograms_of(entry, kg, day, per_kg, scale):
    """
    What an entry weighs, or None where nothing says: from the item's own
    conversion where it has one (`per_kg` remembers each item's), else
    from the scale (`weighed()`).
    """
    item = entry.work_order.item
    if kg is not None:
        if item.pk not in per_kg:
            try:
                per_kg[item.pk] = item.unit_factor(kg, day)  # item units in one kilogramme
            except ValidationError:
                per_kg[item.pk] = None
        if per_kg[item.pk]:
            try:
                return entry.uom.convert_to(entry.quantity_produced, item.uom) / per_kg[item.pk]
            except ValidationError:
                pass
    return scale.get(entry.pk)


def _energy_by_centre(day):
    """kWh, idle kWh and whether any meter went unread, by the work centre the meter serves."""
    centre_of = {}
    for meter in EnergyMeter.objects.select_related("machine"):
        centre_of[meter.pk] = meter.work_centre_id or meter.machine.work_centre_id
    found = {}
    for row in summary(day, day):
        centre_id = centre_of[row["meter"]]
        so_far = found.setdefault(centre_id, {"kwh": ZERO, "idle_kwh": ZERO, "unread": False})
        so_far["kwh"] += row["kwh"]
        so_far["idle_kwh"] += row["idle_kwh"]
        so_far["unread"] = so_far["unread"] or row["days_unread"] > 0
    return found


def daily_production(day):
    kg = kilogramme()
    entries = ProductionEntry.objects.filter(
        posted=True, voided_at__isnull=True, entry_date=day,
    ).select_related("work_order__item__uom", "uom")
    by_centre_unit = defaultdict(lambda: {"made": ZERO, "scrapped": ZERO, "kg": ZERO, "weighed": False})
    per_kg, scale = {}, weighed(entries)
    for entry in entries:
        cell = by_centre_unit[(entry.work_centre_id, entry.uom.code)]
        cell["made"] += entry.quantity_produced
        cell["scrapped"] += entry.quantity_scrapped
        weight = kilograms_of(entry, kg, day, per_kg, scale)
        if weight is not None:
            cell["kg"] += weight
            cell["weighed"] = True
    energy = _energy_by_centre(day)

    cells_of = defaultdict(list)
    for (centre_id, unit), cell in sorted(by_centre_unit.items(), key=lambda kv: kv[0][1]):
        cells_of[centre_id].append((unit, cell))
    rows = []
    for centre in WorkCentre.objects.filter(is_active=True).order_by("code"):
        cells = cells_of.get(centre.pk) or [("", None)]
        drew = energy.get(centre.pk)
        for unit, cell in cells:
            made = cell["made"] if cell else ZERO
            scrapped = cell["scrapped"] if cell else ZERO
            weight = cell["kg"] if cell and cell["weighed"] else None
            whole = made + scrapped
            rows.append({
                "id": f"{centre.pk}-{unit}", "code": centre.code, "name": centre.name, "unit": unit,
                "made": made, "scrapped": scrapped,
                "waste_percent": (scrapped / whole * 100).quantize(PERCENT) if whole else None,
                "kg": weight.quantize(THOUSANDTH) if weight is not None else None,
                "kwh": drew["kwh"].quantize(THOUSANDTH) if drew else None,
                "idle_kwh": drew["idle_kwh"].quantize(THOUSANDTH) if drew else None,
                "kwh_per_kg": (drew["kwh"] / weight).quantize(THOUSANDTH) if drew and weight else None,
                "unread": drew["unread"] if drew else False,
            })
    return rows
