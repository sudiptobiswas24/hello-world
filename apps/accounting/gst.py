"""
India's goods and services tax: who is registered where, and which of
its taxes a supply attracts.

The tax engine already does the arithmetic — rates, compounding,
rounding, posting to collected and paid accounts — and fiscal positions
already swap one tax for another. What GST adds is a rule for WHICH
taxes: a supply inside one state bears central and state tax, half the
rate each; a supply across a state line bears integrated tax at the
whole rate. The same sack, the same price, two different sets of taxes,
decided by where the goods go. Getting it wrong is not a rounding error:
tax collected under the wrong head has to be paid again under the right
one and the first payment refunded, which takes months.

So nothing here computes a rate. It decides, from the company's state
and the party's, whether the inter-state mapping applies, and the
existing engine does the rest. Rates are configured as ordinary taxes —
CGST 9% and SGST 9% on an intra-state line, mapped to IGST 18% by the
inter-state fiscal position — because GST rates change by notification
and a rate written into code is a rate somebody forgets to change.

**Where the goods go is the party's registered state** in this version:
place of supply for goods is the delivery destination, which for a
buyer that ships to its own registered address is the same thing. A
buyer whose goods go to another state needs the place of supply on the
document, which is not built yet and is said here rather than assumed.
"""

import re

from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import AuditModel

# State and union territory codes as the GST network numbers them —
# the first two characters of every GSTIN. Checked against the GSTN
# list when this was written; a new territory is a new row here.
STATES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam",
    "19": "West Bengal", "20": "Jharkhand", "21": "Odisha",
    "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra",
    "29": "Karnataka", "30": "Goa", "31": "Lakshadweep", "32": "Kerala",
    "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana",
    "37": "Andhra Pradesh", "38": "Ladakh", "97": "Other Territory",
}
STATE_CHOICES = sorted(STATES.items())

# Place of supply for a supply out of India. Not a state a party can be
# in, so not in STATES: it follows from an overseas registration.
OVERSEAS_PLACE = "96"

_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_SHAPE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_HSN = re.compile(r"^([0-9]{4}|[0-9]{6}|[0-9]{8})$")


def gstin_check_character(gstin):
    """
    The fifteenth character of a GSTIN, from the first fourteen.

    A base-36 Luhn: alternate characters doubled, each product folded
    back into base 36, the total taken to the next multiple of 36.
    Checked against published GSTINs, the e-invoice sandbox's among
    them, before it was trusted.
    """
    total = 0
    for index, character in enumerate(gstin[:14]):
        value = _CHARS.index(character) * (2 if index % 2 else 1)
        total += value // 36 + value % 36
    return _CHARS[(36 - total % 36) % 36]


def validate_gstin(gstin):
    """
    Refuse a GSTIN that cannot be real, and say why.

    Its shape, its state and its check character. A typing slip in a
    GSTIN is not caught by the buyer or the tax office until the buyer's
    input credit fails to match — months later, on somebody else's
    return — so it is caught here instead.
    """
    gstin = (gstin or "").strip().upper()
    if not _SHAPE.match(gstin):
        raise ValidationError(
            f"{gstin or 'A blank'} is not the shape of a GSTIN: two digits of "
            "state, a ten-character PAN, an entity number, Z, and a check "
            "character."
        )
    if gstin[:2] not in STATES:
        raise ValidationError(f"{gstin} starts with {gstin[:2]}, which is no state.")
    if gstin_check_character(gstin) != gstin[14]:
        raise ValidationError(
            f"{gstin} fails its check character — a character is mistyped "
            "somewhere in it."
        )
    return gstin


def validate_hsn(code):
    """HSN for goods and SAC for services: four, six or eight digits."""
    code = (code or "").strip()
    if code and not _HSN.match(code):
        raise ValidationError(
            f"{code} is not an HSN or SAC code: four, six or eight digits."
        )
    return code


class GstRegistration(models.TextChoices):
    REGULAR = "regular", "Registered, regular"
    COMPOSITION = "composition", "Registered, composition"
    SEZ = "sez", "Special economic zone"
    UNREGISTERED = "unregistered", "Unregistered"
    OVERSEAS = "overseas", "Overseas"


class GstSettings(AuditModel):
    """
    The company's own registration, and the mapping a supply across a
    state line takes.

    Here rather than on the company, because the company is the kernel's
    and knows nothing of any country's tax. Absent, nothing changes:
    a plant outside India, or one not yet set up, taxes exactly as it
    did.
    """

    gstin = models.CharField(max_length=15)
    state = models.CharField(
        max_length=2, choices=STATE_CHOICES, editable=False,
        help_text="Taken from the GSTIN, never typed: the two cannot "
                  "disagree if one is read off the other.",
    )
    interstate_position = models.ForeignKey(
        "accounting.FiscalPosition", on_delete=models.PROTECT, related_name="+",
        help_text="What a supply across a state line maps its taxes through "
                  "— central and state tax to integrated tax at the whole "
                  "rate.",
    )
    is_active = models.BooleanField(default=True)
    b2cl_limit = models.DecimalField(
        max_digits=18, decimal_places=2, default=100000,
        help_text="An inter-state invoice to an unregistered buyer above this "
                  "is reported invoice by invoice (B2CL) rather than in the "
                  "summary. 1,00,000 since August 2024; a setting because it "
                  "is set by notification.",
    )

    class Meta:
        verbose_name = "GST settings"
        verbose_name_plural = "GST settings"

    def __str__(self):
        return f"GST {self.gstin}"

    def save(self, *args, **kwargs):
        self.gstin = validate_gstin(self.gstin)
        self.state = self.gstin[:2]
        if not self.pk and GstSettings.objects.exists():
            raise ValidationError(
                "The company has one GST registration here. A plant registered "
                "in a second state is a second company to this system."
            )
        if self.pk:
            before = GstSettings.objects.filter(pk=self.pk).values_list("gstin", flat=True).first()
            if before is not None and before != self.gstin and _anything_recorded():
                # Returns compare each document's frozen place of supply
                # with this state. Changing it re-reads every past sale.
                raise ValidationError(
                    f"Documents have recorded tax under {before}. A different "
                    "GSTIN is a different registration, and last month's "
                    "returns must not change with it."
                )
        super().save(*args, **kwargs)

    @classmethod
    def active(cls):
        return cls.objects.filter(is_active=True).first()


def _anything_recorded():
    """Whether any posted document froze its taxes. Asked of the models
    that record them, without this module importing sales or purchasing."""
    from django.apps import apps

    from .mixins import PostedTaxDocumentMixin

    return any(
        model.objects.filter(taxes_recorded=True).exists()
        for model in apps.get_models()
        if issubclass(model, PostedTaxDocumentMixin)
    )


def gst_taxes(profile, taxes):
    """
    The taxes a supply to or from this party bears, under GST.

    Inter-state when the party's state is not the company's; intra-state
    otherwise. Called only for a party with no fiscal position of its
    own, which outranks the state line.
    """
    settings = GstSettings.active()
    if settings is None:
        return list(taxes)
    state = profile.place_of_supply()
    if not state:
        raise ValidationError(
            f"{profile.party} has no state on its tax profile, so there is no "
            "knowing whether this supply is inside the state or across a "
            "state line — and the two bear different taxes. Give it a GSTIN "
            "or, if unregistered, a state."
        )
    if state != settings.state:
        return settings.interstate_position.map_taxes(taxes)
    return list(taxes)
