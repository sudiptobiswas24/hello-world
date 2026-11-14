"""
What GST returns need that no other module holds.

A return is compiled, on request, from what each document recorded when
it posted (see returns.py), until it is filed. A filed return is then a
fact (O386): its figures are kept as they stood when the filing was
recorded (FiledReturnFigures), and a later void or note moves the books,
not what was filed.
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
from .gstr2b import Gstr2bLine, Gstr2bStatement  # noqa: E402,F401
from .reverse_charge import (  # noqa: E402,F401
    GstCashPayment, GstCashPaymentRow, PaymentVoucher, ReverseChargeCredit, SelfInvoice)


class FiledReturnFigures(AuditModel):
    """
    What a filed GSTR-1 or GSTR-3B reported, kept when its filing is recorded (O386, mistake 4): the
    return as compiled then, every Decimal and date kept exactly (returns.kept / revived). Read back
    in place of compiling the month again. A filing recorded when the return could not be compiled
    keeps nothing and says why; that month then reads as the books stand, said so.
    """

    filing = models.OneToOneField("accounting.GstReturnFiled", on_delete=models.PROTECT, related_name="figures",
                                  editable=False)
    figures = models.JSONField(null=True, editable=False)
    not_kept = models.TextField(blank=True, editable=False)

    def __str__(self):
        return f"{self.filing} as filed"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            from django.core.exceptions import ValidationError

            raise ValidationError("What a filed return reported is kept as it was; amend it in a later return.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from django.core.exceptions import ValidationError

        raise ValidationError("What a filed return reported is kept as it was; amend it in a later return.")
