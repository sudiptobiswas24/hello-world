"""
The order to plan things in, and where that order does not exist.

Material requirements planning has one structural precondition: an
item's demand must be complete before it is netted. Net the polymer
first and you net it against the tape demand you had at the time, then
raise a tape order that asks for more polymer, and the polymer answer
is already wrong. The fix is old and simple — give every item a level,
plan level by level from the top, and an item is only reached once
everything that could ask for it has already asked.

**This plant's graph has a cycle in it, so no such order exists.**
Tape consumes regrind and produces regrind; fabric waste goes back the
same way. There is no sequence in which regrind is planned after
everything that wants it, because one of the things that wants it is
the thing it comes out of. Pretending otherwise gives an infinite
walk, and clamping the walk without saying so gives a number that
looks like a level and is not.

So the walk cuts the link that closes the cycle, exactly as
`bom.explode()` does, and it says which link it cut. A cut link is not
a rounding error to be swallowed: it means the regrind a tape run will
consume is netted against the regrind already on the floor and against
what other runs will throw off, and not against a fresh extrusion
raised to produce it. That is also what the plant does, which is why
the cut is the right one — but a planner is owed the sentence, not a
silently truncated graph.
"""

from collections import namedtuple

from apps.manufacturing.bom import default_bom_for

Cut = namedtuple("Cut", "item parent path")
Levels = namedtuple("Levels", "code_of cuts")

# A level this deep is a graph that has gone wrong rather than a plant
# that makes something complicated. Printed laminated sacks run to six.
MAX_DEPTH = 40


def _walk(bom, depth, path, code_of, cuts):
    for component in bom.components.select_related("item").all():
        item = component.item
        if item.pk in path:
            # The cut link leaves the graph entirely, levels included. A
            # cut that still pushed the item's level down would send it
            # below its own consumer and have the planner net it before
            # the demand existed — the exact failure low-level coding is
            # for, reintroduced by the machinery meant to avoid it.
            cuts.append(Cut(item=item, parent=bom.item, path=path + (item.pk,)))
            continue
        known = code_of.get(item.pk)
        code_of[item.pk] = max(known or 0, depth + 1)
        # Re-entering a sub-tree that is already at least this deep can
        # only confirm what it already says, and on a wide graph that
        # re-walk is the whole cost. Depth is what pushes a level down,
        # so a shallower arrival changes nothing below it.
        if known is not None and known >= depth + 1:
            continue
        if depth + 1 >= MAX_DEPTH:
            continue
        below = default_bom_for(item)
        if below is not None:
            _walk(below, depth + 1, path + (item.pk,), code_of, cuts)


def low_level_codes():
    """
    Every item's depth below the deepest thing that needs it, and the
    links that had to be cut to answer.

    Zero is a finished good nothing else consumes. An item that appears
    at two depths takes the deeper one, which is the whole point: a
    carton used both on the sack line and inside a pallet assembly must
    not be netted until both have asked.

    By-products do not get a level from being a by-product. Coming off
    a run is a supply, not a requirement, and an item that is only ever
    an output sits at whatever depth it is consumed at — zero if it is
    consumed nowhere.
    """
    from apps.manufacturing.bom import BillOfMaterials

    code_of = {}
    cuts = []
    boms = (
        BillOfMaterials.objects.filter(is_default=True, is_active=True)
        .select_related("item")
        .order_by("item__sku", "pk")
    )
    for bom in boms:
        code_of.setdefault(bom.item_id, 0)
        _walk(bom, 0, (bom.item_id,), code_of, cuts)
    return Levels(code_of=code_of, cuts=cuts)


def level_of(codes, item):
    """
    An item nobody makes and nobody consumes is at the top.

    Said here rather than with a `.get(pk, 0)` at each call site,
    because the default is a claim about the plant — an item no bill of
    materials mentions is planned against its own demand and nothing
    else — and a bare zero does not read as one.
    """
    return codes.code_of.get(getattr(item, "pk", item), 0)
