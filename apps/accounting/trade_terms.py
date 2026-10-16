"""
How goods travel between a seller and a buyer, worded for either side:
both trading modules record them on their orders and print them on their
papers, so they live here, where both may read them.
"""

from django.db import models


class FreightTerms(models.TextChoices):
    EX_WORKS = "ex_works", "Ex works: the buyer collects"
    FOR_DESTINATION = "for_destination", "FOR destination: the seller delivers, freight paid"
    TO_PAY = "to_pay", "To pay: the seller sends, the buyer pays the transporter"


class Incoterm(models.TextChoices):
    EXW = "EXW", "EXW: ex works"
    FCA = "FCA", "FCA: free carrier"
    FOB = "FOB", "FOB: free on board"
    CFR = "CFR", "CFR: cost and freight"
    CIF = "CIF", "CIF: cost, insurance and freight"
    DAP = "DAP", "DAP: delivered at place"
    DDP = "DDP", "DDP: delivered duty paid"


def freight_meta(order, port_label="Port of discharge"):
    """
    How the goods travel, as the order recorded it, for its papers: not
    "FOB Jebel Ali", since an F-term names the port of loading, so the
    port goes on its own line under the name the order gives it.
    """
    meta = []
    if order.freight_terms:
        meta.append(["Freight", order.get_freight_terms_display().split(":")[0]])
    if order.incoterm:
        meta.append(["Incoterm", order.incoterm])
    port = getattr(order, "port_of_discharge", "") or getattr(order, "port_of_loading", "")
    if port:
        meta.append([port_label, port])
    return meta
