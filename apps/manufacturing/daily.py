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
    unit = UnitOfMeasure.objects.filter(code__iexact="kg", category=UnitOfMeasureCategory.WEIGHT).first()
    return unit


def kilograms_of(entry, kg, day):
    """What an entry weighs, or None where nothing says."""
    item = entry.work_order.item
    if kg is not None:
        try:
            per_kg = item.unit_factor(kg, day)  # item units in one kilogramme
            in_item_units = entry.uom.convert_to(entry.quantity_produced, item.uom)
        except ValidationError:
            per_kg = None
        if per_kg:
            return in_item_units / per_kg
    weighed = FabricRoll.objects.filter(entry=entry).aggregate(kg=Sum("net_weight_kg"))["kg"]
    if weighed is None:
        weighed = TapeDoff.objects.filter(entry=entry).aggregate(kg=Sum("net_kg"))["kg"]
    return weighed


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
    for entry in entries:
        cell = by_centre_unit[(entry.work_centre_id, entry.uom.code)]
        cell["made"] += entry.quantity_produced
        cell["scrapped"] += entry.quantity_scrapped
        weight = kilograms_of(entry, kg, day)
        if weight is not None:
            cell["kg"] += weight
            cell["weighed"] = True
    energy = _energy_by_centre(day)

    rows = []
    for centre in WorkCentre.objects.filter(is_active=True).order_by("code"):
        cells = [(unit, cell) for (centre_id, unit), cell in sorted(by_centre_unit.items(), key=lambda kv: kv[0][1])
                 if centre_id == centre.pk] or [("", None)]
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
