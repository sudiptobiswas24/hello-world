from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction

CRUD = ("add", "change", "delete", "view")


def crud(app_label, model, actions=CRUD):
    return [f"{app_label}.{action}_{model}" for action in actions]


# Roles are built around segregation of duties: the people who prepare
# documents are not automatically the people who can post them to the
# ledger or to stock.
ROLES = {
    "Bookkeeper": [
        *crud("accounting", "account"),
        *crud("accounting", "journalentry"),
        *crud("accounting", "journalline"),
        # deliberately NOT accounting.post_journalentry
    ],
    "Controller": [
        *crud("accounting", "account"),
        *crud("accounting", "journalentry"),
        *crud("accounting", "journalline"),
        "accounting.post_journalentry",
        *crud("core", "currency"),
        *crud("core", "exchangerate"),
        *crud("core", "paymentterms"),
        *crud("core", "documentsequence"),
        *crud("core", "company", actions=("change", "view")),
        *crud("accounting", "tax"),
        *crud("accounting", "taxgroup"),
        *crud("accounting", "fiscalposition"),
        *crud("accounting", "fiscalpositiontaxmapping"),
        *crud("accounting", "partytaxprofile"),
        *crud("accounting", "payment"),
        "accounting.post_payment",
    ],
    "Sales Rep": [
        *crud("sales", "salesorder"),
        *crud("sales", "salesorderline"),
        *crud("sales", "invoice"),
        *crud("sales", "invoiceline"),
        *crud("core", "party", actions=("add", "change", "view")),
        *crud("core", "address", actions=("add", "change", "view")),
        *crud("core", "contact", actions=("add", "change", "view")),
        *crud("core", "paymentterms", actions=("view",)),
        *crud("sales", "pricelist", actions=("view",)),
        *crud("sales", "pricelistitem", actions=("view",)),
        *crud("sales", "customerprofile", actions=("view",)),
        *crud("sales", "quotation"),
        *crud("sales", "quotationline"),
        # deliberately NOT sales.post_invoice
    ],
    "AR Manager": [
        *crud("sales", "salesorder"),
        *crud("sales", "salesorderline"),
        *crud("sales", "invoice"),
        *crud("sales", "invoiceline"),
        *crud("sales", "invoicepayment"),
        *crud("sales", "pricelist"),
        *crud("sales", "pricelistitem"),
        *crud("sales", "customerprofile"),
        *crud("sales", "quotation"),
        *crud("sales", "quotationline"),
        *crud("sales", "dunninglevel"),
        *crud("sales", "dunningnotice", actions=("view",)),
        *crud("accounting", "payment"),
        "sales.post_invoice",
        "accounting.post_payment",
    ],
    "Purchasing Clerk": [
        *crud("purchasing", "purchaseorder"),
        *crud("purchasing", "purchaseorderline"),
        *crud("purchasing", "bill"),
        *crud("purchasing", "billline"),
        *crud("core", "party", actions=("add", "change", "view")),
        *crud("core", "address", actions=("add", "change", "view")),
        *crud("core", "contact", actions=("add", "change", "view")),
        *crud("core", "partybankaccount", actions=("add", "change", "view")),
        *crud("core", "paymentterms", actions=("view",)),
        # deliberately NOT purchasing.post_bill
    ],
    "AP Manager": [
        *crud("purchasing", "purchaseorder"),
        *crud("purchasing", "purchaseorderline"),
        *crud("purchasing", "bill"),
        *crud("purchasing", "billline"),
        "purchasing.post_bill",
    ],
    "Warehouse Staff": [
        *crud("inventory", "warehouse", actions=("view",)),
        *crud("inventory", "item", actions=("view",)),
        *crud("inventory", "stockmovement", actions=("add", "view")),
        *crud("purchasing", "goodsreceipt"),
        *crud("purchasing", "goodsreceiptline"),
        "purchasing.post_goodsreceipt",
        *crud("sales", "delivery"),
        *crud("sales", "deliveryline"),
        "sales.post_delivery",
    ],
    "HR Admin": [
        *crud("hr", "department"),
        *crud("hr", "employee"),
        *crud("hr", "leaverequest"),
        "hr.decide_leaverequest",
    ],
    "Employee Self Service": [
        *crud("hr", "leaverequest", actions=("add", "view")),
        # deliberately NOT hr.decide_leaverequest
    ],
}


class Command(BaseCommand):
    help = "Create or update the default role groups and their permissions."

    @transaction.atomic
    def handle(self, *args, **options):
        verbosity = options["verbosity"]
        for role_name, permission_names in ROLES.items():
            group, created = Group.objects.get_or_create(name=role_name)
            permissions = []
            for name in permission_names:
                app_label, codename = name.split(".", 1)
                try:
                    permissions.append(
                        Permission.objects.get(
                            content_type__app_label=app_label, codename=codename
                        )
                    )
                except Permission.DoesNotExist:
                    self.stderr.write(f"  missing permission: {name}")
            group.permissions.set(permissions)
            if verbosity:
                verb = "created" if created else "updated"
                self.stdout.write(f"{verb} {role_name} ({len(permissions)} permissions)")
