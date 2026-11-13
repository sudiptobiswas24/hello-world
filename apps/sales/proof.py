"""
The signed copy of a delivery: its proof of delivery, kept as a file.

The receipt on a delivery (Delivery.record_receipt) says when the customer
received it, who signed and their GRN number. The paper they signed is
kept with it through the attachment facility (apps/core/attachments.py),
as a file whose purpose is "proof_of_delivery": a scan or a phone
photograph, PDF, JPEG or PNG, up to the facility's size.

- Who and when are the attachment's own (created_by, created_at), and the
  delivery's history says "Attached: Proof of delivery: pod.pdf".
- Read by whoever may read the delivery, through the attachment API: a
  login limited to its own customers reads only theirs.
- Kept by whoever may record the receipt (sales.change_delivery), on a
  delivery that went out and whose receipt is recorded: the paper is the
  receipt's, and a scan with no date and no signatory is not a proof.
- Never replaced in place. A better scan is added; the latest is current
  and the earlier ones stay listed. Removed only as any attachment is: by
  whoever added it, or someone who may remove attachments; the removal is
  in the delivery's history, and the one before it is current again.
- The reverse: a receipt is corrected by recording it again, never
  cleared, so a proof never loses its receipt; a delivery returned later
  keeps the proof of what was first signed for. A return has none.
"""

from django.core.exceptions import PermissionDenied, ValidationError

PROOF_OF_DELIVERY = "proof_of_delivery"
KINDS = (".pdf", ".jpg", ".jpeg", ".png")


def may_keep_proof(delivery, user):
    """Refuses, in words, a signed copy this delivery cannot have or this person may not keep."""
    if not user.has_perm("sales.change_delivery"):
        raise PermissionDenied("Keeping a delivery's signed copy takes what recording its receipt takes; "
                               "it is not yours.")
    if not delivery.posted or delivery.reverses_id:
        raise ValidationError("Only a delivery that went out has a proof of delivery.")
    if delivery.received_on is None:
        raise ValidationError("Record the receipt first (when the customer received it and who signed); "
                              "the signed copy is kept with it.")


def register():
    from apps.core.attachments import register_purpose

    register_purpose(PROOF_OF_DELIVERY, label="Proof of delivery", model="sales.delivery", extensions=KINDS,
                     check=may_keep_proof)
