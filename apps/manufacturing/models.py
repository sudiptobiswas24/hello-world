"""
Manufacturing: what the plant makes, and what it takes.

Split in two on purpose. `bom.py` holds quantities of items against an
item and knows nothing about any industry; `woven.py` knows what a
denier is and computes those quantities from a sack specification.
Everything downstream — work orders, material issue, variance — talks
to the first and never to the second, so the plant that starts making
something other than sacks does not have to unpick its costing.
"""

from .bom import (  # noqa: F401
    BillOfMaterials,
    BomByproduct,
    BomComponent,
    ByproductValuation,
    Requirement,
    default_bom_for,
    explode,
    material_balance,
    net_requirements,
)
from .woven import (  # noqa: F401
    BagSpecification,
    FabricSpecification,
    TapeSpecification,
    Weave,
)
