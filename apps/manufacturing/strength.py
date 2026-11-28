"""
What a tape's filler will do to its strength, learnt from the plant's own
batches.

Calcium carbonate is cheaper than polypropylene and weaker, so every
point of filler buys price with tenacity and stretch. How much is a
property of this plant's lines, polymer and draw, not of a textbook, so
the answer is fitted from what has been measured here: for each tape
batch inspected for tenacity or elongation, the filler of the
specification its run was made to, against the batch's measured mean.

**A straight line, and its error with it.** Ordinary least squares over
batches, reported with how many there were, how well the line fits (R²)
and how far batches scatter around it. A prediction is the line's figure
and a one-sided 95% lower bound for a single batch (upper too, for
elongation's ceiling); against the customer's minimum the verdict is
*meets* when even the bound clears it, *doubtful* when only the line
does, *fails* when the line itself falls short.

**What it will not pretend.**
- Fewer than five batches, or all at one filler: no slope can be seen,
  and nothing is predicted.
- A batch made by runs at different fillers, or by a run to no tape
  specification, is left out and listed: its strength belongs to no one
  figure.
- A filler outside what was measured is extrapolation, and says so. The
  line is a description of the range it came from.
"""

import math
from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError

MIN_BATCHES = 5
FOUR = Decimal("0.0001")

# One-sided 95% Student t by degrees of freedom. Beyond the table the
# nearest smaller entry is used, which is the larger t: wider, not
# narrower, when unsure.
_T95 = {1: 6.314, 2: 2.920, 3: 2.353, 4: 2.132, 5: 2.015, 6: 1.943, 7: 1.895, 8: 1.860,
        9: 1.833, 10: 1.812, 11: 1.796, 12: 1.782, 13: 1.771, 14: 1.761, 15: 1.753,
        16: 1.746, 17: 1.740, 18: 1.734, 19: 1.729, 20: 1.725, 21: 1.721, 22: 1.717,
        23: 1.714, 24: 1.711, 25: 1.708, 26: 1.706, 27: 1.703, 28: 1.701, 29: 1.699,
        30: 1.697, 40: 1.684, 60: 1.671, 120: 1.658}


def _t(df):
    return _T95[max(key for key in _T95 if key <= df)]


def history(measure):
    """[(filler %, measured mean, lot code)] and the lots left out, for 'tenacity' or 'elongation'."""
    from apps.quality.models import Reading
    from apps.quality.release import latest_inspection

    from .demand import runs_that_made
    from .woven import TapeSpecification

    by_inspection = defaultdict(list)
    for reading in Reading.objects.filter(
            plan_line__derived_from=measure, inspection__posted=True,
            inspection__voided_at__isnull=True, value__isnull=False,
    ).select_related("inspection__lot"):
        by_inspection[reading.inspection].append(reading.value)
    pairs, mixed = [], []
    for inspection, values in by_inspection.items():
        lot = inspection.lot
        standing = latest_inspection(lot)
        if standing is None or standing.pk != inspection.pk:
            continue
        fillers = set()
        for run in runs_that_made(lot):
            specs = list(TapeSpecification.objects.filter(bom=run.bom))
            # A run to no specification had a filler nobody wrote down:
            # the batch's strength cannot be put against any figure.
            fillers |= {spec.filler_percent for spec in specs} if specs else {None}
        if len(fillers) != 1 or None in fillers:
            mixed.append(lot.code)
            continue
        pairs.append((fillers.pop(), sum(values, Decimal("0")) / len(values), lot.code))
    return sorted(pairs, key=lambda row: (row[0], row[2])), sorted(mixed)


def fit(measure):
    pairs, mixed = history(measure)
    xs = [float(x) for x, _, _ in pairs]
    ys = [float(y) for _, y, _ in pairs]
    n = len(pairs)
    if n < MIN_BATCHES:
        raise ValidationError(
            f"{n} tape batch{'es' if n != 1 else ''} measured for {measure}; a line needs "
            f"at least {MIN_BATCHES}."
        )
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx == 0:
        raise ValidationError(
            f"Every batch measured for {measure} was at {xs[0]}% filler; what filler does "
            "cannot be seen from one level."
        )
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / sxx
    intercept = mean_y - slope * mean_x
    sse = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    sst = sum((y - mean_y) ** 2 for y in ys)
    return {
        "measure": measure, "batches": n, "left_out": mixed,
        "intercept": intercept, "slope": slope,
        "r_squared": 1 - sse / sst if sst else 1.0,
        "scatter": math.sqrt(sse / (n - 2)) if n > 2 else 0.0,
        "mean_filler": mean_x, "sxx": sxx, "filler_range": (min(xs), max(xs)),
    }


def predict(model, filler):
    """The line's figure at a filler, with a one-sided 95% bound either side."""
    x = float(filler)
    centre = model["intercept"] + model["slope"] * x
    spread = model["scatter"] * math.sqrt(
        1 + 1 / model["batches"] + (x - model["mean_filler"]) ** 2 / model["sxx"])
    margin = _t(model["batches"] - 2) * spread
    low, high = model["filler_range"]

    def four(value):
        return Decimal(str(value)).quantize(FOUR)

    return {
        "filler_percent": Decimal(str(filler)), "predicted": four(centre),
        "lower": four(centre - margin), "upper": four(centre + margin),
        "extrapolated": not low <= x <= high,
    }


def _verdict_floor(prediction, floor):
    if prediction["lower"] >= floor:
        return "meets"
    return "doubtful" if prediction["predicted"] >= floor else "fails"


def _verdict_range(prediction, low, high):
    below = low is not None and prediction["predicted"] < low
    above = high is not None and prediction["predicted"] > high
    if below or above:
        return "fails"
    if (low is not None and prediction["lower"] < low) or (
            high is not None and prediction["upper"] > high):
        return "doubtful"
    return "meets"


def check(spec, filler=None):
    """What the plant's history says a tape at this filler will measure,
    against what the specification demands."""
    filler = spec.filler_percent if filler is None else Decimal(str(filler))
    if not filler.is_finite() or filler < 0 or filler >= 100:
        raise ValidationError("Filler is a percentage of the blend.")
    rows = []
    for measure, wanted in (
            ("tenacity", {"minimum": spec.min_tenacity_gpd}),
            ("elongation", {"minimum": spec.elongation_min_percent,
                            "maximum": spec.elongation_max_percent})):
        try:
            model = fit(measure)
        except ValidationError as refusal:
            rows.append({"measure": measure, "prediction": None, "verdict": None,
                         "why": refusal.messages[0]})
            continue
        prediction = predict(model, filler)
        if measure == "tenacity":
            verdict = (None if wanted["minimum"] is None
                       else _verdict_floor(prediction, wanted["minimum"]))
        elif wanted["minimum"] is None and wanted["maximum"] is None:
            verdict = None
        else:
            verdict = _verdict_range(prediction, wanted["minimum"], wanted["maximum"])
        rows.append({
            "measure": measure, "prediction": prediction, "verdict": verdict,
            "wanted": wanted, "batches": model["batches"], "left_out": model["left_out"],
            "r_squared": Decimal(str(model["r_squared"])).quantize(FOUR),
            "per_point_of_filler": Decimal(str(model["slope"])).quantize(FOUR),
        })
    return rows
