"""
Control charts: whether a process is drifting before a batch fails.

Every inspection already measures a handful of samples and passes or
fails the batch. A GSM creeping up by a gramme a week passes every
batch until the week it does not; the chart is how it is seen coming.

**One inspection is one subgroup.** Its readings for the characteristic
are the sample, its mean the point on the X-bar chart and its range the
point on the R chart. Standing inspections only: a voided one no longer
speaks for its batch.

**Sigma from within the subgroups.** The mean of each subgroup's range
over d2 for its size, so subgroups of different sizes each get their
own limits around the same centre. Where every subgroup is a single
reading, an individuals chart with moving ranges instead.

**Limits from a baseline**, points judged against them. Limits
recomputed from data that includes a drift widen to swallow it, so
limits are set from a period the plant says was in control (by default
everything shown, said as such) and later points are judged against
them. Fewer than twenty baseline subgroups is said to be provisional.

**Signals**, on the X-bar chart: beyond three sigma; nine in a row on
one side of the centre; six in a row rising or falling; two of three
beyond two sigma on the same side. On the range chart: beyond its
limits.

**Capability** against the specification limits frozen on the latest
reading: Cp and Cpk. Said to be meaningless while the process is out of
control, because it is.
"""

import math
from collections import Counter

from django.core.exceptions import ValidationError

from apps.core.models import to_date

# d2 and d3 by subgroup size: the mean and spread of the range of n
# normal samples in units of sigma.
D2 = {2: 1.128, 3: 1.693, 4: 2.059, 5: 2.326, 6: 2.534, 7: 2.704, 8: 2.847, 9: 2.970,
      10: 3.078, 11: 3.173, 12: 3.258, 13: 3.336, 14: 3.407, 15: 3.472, 16: 3.532,
      17: 3.588, 18: 3.640, 19: 3.689, 20: 3.735, 21: 3.778, 22: 3.819, 23: 3.858,
      24: 3.895, 25: 3.931}
D3 = {2: 0.853, 3: 0.888, 4: 0.880, 5: 0.864, 6: 0.848, 7: 0.833, 8: 0.820, 9: 0.808,
      10: 0.797, 11: 0.787, 12: 0.778, 13: 0.770, 14: 0.763, 15: 0.756, 16: 0.750,
      17: 0.744, 18: 0.739, 19: 0.734, 20: 0.729, 21: 0.724, 22: 0.720, 23: 0.716,
      24: 0.712, 25: 0.708}
ENOUGH = 20
MR_D4 = 3.267


def _subgroups(item, characteristic, start=None, end=None):
    from .models import Inspection

    inspections = Inspection.objects.filter(
        posted=True, voided_at__isnull=True, lot__item=item,
        readings__plan_line__characteristic=characteristic,
    ).distinct().select_related("lot").order_by("inspected_on", "id")
    if start:
        inspections = inspections.filter(inspected_on__gte=to_date(start))
    if end:
        inspections = inspections.filter(inspected_on__lte=to_date(end))
    rows = []
    for inspection in inspections:
        readings = list(inspection.readings.filter(
            plan_line__characteristic=characteristic, value__isnull=False).order_by("id"))
        if not readings:
            continue
        values = [float(reading.value) for reading in readings]
        rows.append({"inspection": inspection, "values": values, "n": len(values),
                     "mean": sum(values) / len(values), "range": max(values) - min(values),
                     "limits": (readings[-1].lower_limit, readings[-1].upper_limit)})
    return rows


def _run_rules(points, centre, sigma):
    """Signals per point, by index."""
    signals = [[] for _ in points]
    for index, point in enumerate(points):
        spread = sigma / math.sqrt(point["n"])
        if abs(point["mean"] - centre) > 3 * spread:
            signals[index].append("beyond 3 sigma")
        if index >= 8:
            window = points[index - 8:index + 1]
            if all(p["mean"] > centre for p in window) or all(p["mean"] < centre for p in window):
                signals[index].append("9 in a row on one side")
        if index >= 5:
            window = [p["mean"] for p in points[index - 5:index + 1]]
            steps = [b - a for a, b in zip(window, window[1:])]
            if all(step > 0 for step in steps) or all(step < 0 for step in steps):
                signals[index].append("6 in a row rising or falling")
        if index >= 2:
            window = points[index - 2:index + 1]
            for side in (1, -1):
                far = [p for p in window
                       if side * (p["mean"] - centre) > 2 * sigma / math.sqrt(p["n"])]
                if len(far) >= 2 and side * (point["mean"] - centre) > \
                        2 * sigma / math.sqrt(point["n"]):
                    signals[index].append("2 of 3 beyond 2 sigma")
    return signals


def chart(item, characteristic, start=None, end=None, baseline_end=None):
    rows = _subgroups(item, characteristic, start, end)
    if not rows:
        raise ValidationError(f"No standing inspection of {item} measured "
                              f"{characteristic.code} in that window.")
    too_big = [row for row in rows if row["n"] > max(D2)]
    rows = [row for row in rows if row["n"] <= max(D2)]
    baseline = [row for row in rows if baseline_end is None
                or row["inspection"].inspected_on <= to_date(baseline_end)]
    if len(baseline) < 2:
        raise ValidationError("Limits need at least two subgroups in the baseline.")
    individuals = all(row["n"] == 1 for row in baseline)
    if individuals:
        moving = [abs(b["mean"] - a["mean"]) for a, b in zip(baseline, baseline[1:])]
        mr_bar = sum(moving) / len(moving)
        sigma = mr_bar / D2[2]
    else:
        ranged = [row for row in baseline if row["n"] >= 2]
        sigma = sum(row["range"] / D2[row["n"]] for row in ranged) / len(ranged)
    centre = sum(row["mean"] for row in baseline) / len(baseline)
    signals = _run_rules(rows, centre, sigma)
    in_baseline = {id(row) for row in baseline}
    points = []
    for index, row in enumerate(rows):
        spread = sigma / math.sqrt(row["n"])
        point = {"inspection": row["inspection"].number, "lot": row["inspection"].lot.code,
                 "inspected_on": row["inspection"].inspected_on, "n": row["n"],
                 "mean": row["mean"], "range": row["range"],
                 "ucl": centre + 3 * spread, "lcl": centre - 3 * spread,
                 "in_baseline": id(row) in in_baseline, "signals": list(signals[index])}
        if individuals:
            previous = rows[index - 1]["mean"] if index else None
            point["moving_range"] = abs(row["mean"] - previous) if previous is not None else None
            point["r_ucl"], point["r_lcl"] = MR_D4 * mr_bar, 0.0
            if point["moving_range"] is not None and point["moving_range"] > point["r_ucl"]:
                point["signals"].append("moving range beyond its limit")
        elif row["n"] >= 2:
            d2, d3 = D2[row["n"]], D3[row["n"]]
            point["r_ucl"] = (d2 + 3 * d3) * sigma
            point["r_lcl"] = max(0.0, (d2 - 3 * d3) * sigma)
            if row["range"] > point["r_ucl"] or row["range"] < point["r_lcl"]:
                point["signals"].append("range beyond its limits")
        points.append(point)
    notes = []
    if baseline_end is None:
        notes.append("Limits are set from every subgroup shown; give a baseline the plant "
                      "knows was in control to judge later points against it.")
    if len(baseline) < ENOUGH:
        notes.append(f"{len(baseline)} baseline subgroups; limits are provisional until "
                     f"there are {ENOUGH}.")
    if too_big:
        notes.append(f"{len(too_big)} subgroups of more than {max(D2)} readings are left out; "
                     "a range says little about that many.")
    sizes = Counter(row["n"] for row in rows)
    lower, upper = rows[-1]["limits"]
    capability = None
    if sigma > 0 and (lower is not None or upper is not None):
        mean = centre
        cp = (float(upper) - float(lower)) / (6 * sigma) \
            if lower is not None and upper is not None else None
        sides = [value for value in (
            (float(upper) - mean) / (3 * sigma) if upper is not None else None,
            (mean - float(lower)) / (3 * sigma) if lower is not None else None,
        ) if value is not None]
        capability = {"lower": lower, "upper": upper, "cp": cp, "cpk": min(sides)}
        if any(point["signals"] for point in points if point["in_baseline"]):
            notes.append("The baseline itself signals: capability is meaningless until the "
                         "process is in control.")
    return {"item": item.sku, "characteristic": characteristic.code,
            "chart": "individuals and moving range" if individuals else "X-bar and R",
            "centre": centre, "sigma": sigma, "subgroup_sizes": dict(sizes),
            "baseline_subgroups": len(baseline), "points": points,
            "capability": capability, "notes": notes}
