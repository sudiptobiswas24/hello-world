"""
What GST returns need that no other module holds.

The returns themselves are not stored: they are compiled, on request,
from what each document recorded when it posted (see returns.py). A
stored return is a copy, and a copy drifts from the documents the moment
a credit note lands in the period.
"""

from django.db import models

from apps.core.models import AuditModel, UnitOfMeasure

# Unit quantity codes as the GST network lists them — the unit an HSN
# summary reports quantity in. The common ones; OTH for the rest.
UQC_CHOICES = [
    ("BAG", "Bags"), ("BAL", "Bale"), ("BDL", "Bundles"), ("BOX", "Box"),
    ("BTL", "Bottles"), ("CTN", "Cartons"), ("DOZ", "Dozens"),
    ("GMS", "Grammes"), ("KGS", "Kilograms"), ("KLR", "Kilolitre"),
    ("LTR", "Litres"), ("MTR", "Metres"), ("MTS", "Metric ton"),
    ("NOS", "Numbers"), ("PAC", "Packs"), ("PCS", "Pieces"),
    ("QTL", "Quintal"), ("ROL", "Rolls"), ("SQF", "Square feet"),
    ("SQM", "Square metres"), ("TON", "Tonnes"), ("UNT", "Units"),
    ("OTH", "Others"),
]


class UnitQuantityCode(AuditModel):
    """
    The GST code for one of the company's units.

    Here, not on the unit: the unit is the kernel's and knows nothing of
    any country's returns. A unit with no code reports as OTH, and the
    return says which ones did.
    """

    uom = models.OneToOneField(UnitOfMeasure, on_delete=models.CASCADE, related_name="gst_uqc")
    code = models.CharField(max_length=3, choices=UQC_CHOICES)

    class Meta:
        permissions = [("compile_returns", "Can compile GST returns")]

    def __str__(self):
        return f"{self.uom.code} → {self.code}"


from .einvoice import EInvoice  # noqa: E402,F401
from .ewaybill import EwayBill  # noqa: E402,F401
