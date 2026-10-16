"""
Whether goods meet what a customer's material rules demand: no recovered
waste for a virgin-only buyer, filler no higher and UV no lower than they
allow. Registered with sales when the app loads, and asked when an order
is confirmed and when it ships.

**Recovered waste is not a list anybody keeps.** It is whatever a bill
throws off as a by-product — regrind, loom waste, cutting waste — which
the specifications already name, and it is recognised wherever it turns
up again as an ingredient.

**On the recipe, then on the batches.** Confirming an order reads the
recipe in force: the bill of the item, and of what it is made from,
level by level. Shipping reads the batches that go: which runs made
them and everything those runs drew, tracked or not. A run that drew
something made here without naming the batch cannot show what that was
made of, and a virgin-only shipment says so rather than assuming.
"""

from collections import defaultdict
from decimal import Decimal

MAX_DEPTH = 6


def is_recovered(item):
    from .bom import BomByproduct

    return BomByproduct.objects.filter(item=item).exists()


def _recipe(item, on_date):
    """Every bill under the item on the day, and the tape specifications among them."""
    from .bom import default_bom_for
    from .woven import TapeSpecification

    boms, tapes = [], []

    def walk(current, path):
        bom = default_bom_for(current, on_date)
        if bom is None or current.pk in path or len(path) > MAX_DEPTH:
            return
        boms.append(bom)
        tape = TapeSpecification.objects.filter(bom=bom).first()
        if tape is not None:
            tapes.append(tape)
        for component in bom.components.select_related("item"):
            walk(component.item, path + (current.pk,))

    walk(item, ())
    return boms, tapes


def _lot_problems(lot, seen, depth=0):
    from .bom import default_bom_for
    from .demand import runs_that_made

    if lot.pk in seen or depth > MAX_DEPTH:
        return []
    seen.add(lot.pk)
    problems = []
    for run in runs_that_made(lot):
        drawn = defaultdict(lambda: Decimal("0"))
        lines = {}
        for issue in run.posted_issues():
            for line in issue.lines.select_related("item", "lot", "uom"):
                key = (line.item_id, line.lot_id)
                drawn[key] += line.stock_quantity() * issue.sign()
                lines[key] = line
        for key, quantity in drawn.items():
            if quantity <= 0:
                continue
            line = lines[key]
            batch = f" batch {line.lot.code}" if line.lot_id else ""
            if is_recovered(line.item):
                problems.append(
                    f"Lot {lot.code} was made by {run.number} from {line.item.sku}{batch}, "
                    "which is recovered waste."
                )
            elif line.lot_id:
                problems.extend(_lot_problems(line.lot, seen, depth + 1))
            elif default_bom_for(line.item, run.scheduled_start or None) is not None:
                problems.append(
                    f"Lot {lot.code} was made by {run.number} from {line.item.sku} drawn "
                    "without a batch, so what that was made of cannot be shown."
                )
    return problems


def material_problems(profile, item, lots, on_date):
    problems = []
    if profile.virgin_only and is_recovered(item):
        problems.append(f"{item.sku} is itself recovered waste.")
    boms, tapes = _recipe(item, on_date)
    if profile.virgin_only:
        for bom in boms:
            for component in bom.components.select_related("item"):
                if is_recovered(component.item):
                    problems.append(
                        f"{bom.item.sku}'s recipe uses {component.item.sku}, which is "
                        "recovered waste."
                    )
        seen = set()
        for lot in lots:
            problems.extend(_lot_problems(lot, seen))
    if profile.max_filler_percent is not None or profile.min_uv_percent is not None:
        if not tapes:
            problems.append(
                f"Nothing in {item.sku}'s recipe on {on_date} states its tape's filler "
                "or UV, so neither can be shown."
            )
        for tape in tapes:
            limit = profile.max_filler_percent
            if limit is not None and tape.filler_percent > limit:
                problems.append(
                    f"Tape {tape.code} carries {tape.filler_percent}% filler; the most "
                    f"allowed is {limit}%."
                )
            floor = profile.min_uv_percent
            if floor is not None and tape.uv_percent < floor:
                problems.append(
                    f"Tape {tape.code} carries {tape.uv_percent}% UV stabiliser; at least "
                    f"{floor}% is required."
                )
    return problems
