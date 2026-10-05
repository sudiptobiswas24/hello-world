from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

CRUD = ("add", "change", "delete", "view")


def crud(app_label, model, actions=CRUD):
    return [f"{app_label}.{action}_{model}" for action in actions]


def view(app_label, *models):
    return [f"{app_label}.view_{model}" for model in models]


def full(app_label, *models):
    return [name for model in models for name in crud(app_label, model)]


# What every office role reads to do anything at all: the units, the
# currencies, the parties, the items and where they are kept. Reading
# took no permission until the review; now it does, and a rep who cannot
# see an item cannot put it on an order.
REFERENCE = [
    *view("core", "currency", "exchangerate", "unitofmeasure", "country", "party",
          "address", "contact", "paymentterms", "paymenttermsline", "company"),
    *view("inventory", "item", "itemunit", "warehouse", "lot"),
    *view("accounting", "account", "tax", "taxgroup", "fiscalposition", "partytaxprofile"),
]

# What anyone on the floor or planning it reads: how things are made.
HOW_IT_IS_MADE = [
    *view("manufacturing", "bagspecification", "fabricspecification", "tapespecification",
          "filmspecification", "linerspecification", "bagcoatingline", "billofmaterials",
          "bomcomponent", "bombyproduct", "bomsubstitute", "routing", "routingoperation",
          "alternaterouting", "workcentre", "machine", "shift", "tool", "printdesign",
          "coretype", "scrapreason", "downtimereason", "setupfamily", "changeoverrule"),
]


# Roles are built around segregation of duties: the people who prepare
# documents are not automatically the people who can post them to the
# ledger or to stock.
ROLES = {
    "Bookkeeper": [
        *REFERENCE,
        *crud("assets", "fixedasset", actions=("view",)),
        *crud("assets", "depreciationentry", actions=("view",)),
        # deliberately NOT assets.dispose_fixedasset: writing an asset off
        # the books is a Controller decision.
        *crud("accounting", "bankstatement", actions=("add", "change", "view")),
        *crud("accounting", "bankstatementline", actions=("add", "change", "view")),
        # deliberately NOT accounting.close_bankstatement: whoever keys the
        # statement in should not also be the one who signs it off.
        *crud("accounting", "account"),
        *crud("accounting", "journalentry"),
        *crud("accounting", "journalline"),
        # deliberately NOT accounting.post_journalentry
    ],
    "Controller": [
        *REFERENCE,
        *crud("accounting", "account"),
        *crud("accounting", "journalentry"),
        *crud("accounting", "journalline"),
        "accounting.post_journalentry",
        *crud("core", "currency"),
        *crud("core", "exchangerate"),
        *crud("core", "paymentterms"),
        *crud("core", "paymenttermsline"),
        *crud("core", "documentsequence"),
        *crud("core", "company", actions=("change", "view")),
        *crud("accounting", "tax"),
        *crud("accounting", "taxgroup"),
        *crud("accounting", "chargetype"),
        *crud("accounting", "fiscalposition"),
        *crud("accounting", "fiscalpositiontaxmapping"),
        *crud("accounting", "partytaxprofile"),
        *crud("accounting", "payment"),
        "accounting.post_payment",
        # Payroll: posts what the Payroll Officer worked out, pays it, and
        # pays PF, ESI and tax over.
        *view("hr", "employee", "paycomponent", "paycomponentslab", "employeecompensation",
              "payrun", "payslip", "payslipline"),
        "hr.post_payrun",
        *crud("hr", "statutoryremittance", actions=("add", "delete", "view")),
        *crud("assets", "assetcategory"),
        *crud("assets", "fixedasset"),
        *crud("assets", "depreciationentry", actions=("view",)),
        "assets.dispose_fixedasset",
        *crud("accounting", "bankstatement"),
        *crud("accounting", "bankstatementline"),
        "accounting.close_bankstatement",
        # Giving up on a receivable is an expense decision, so it sits with
        # the Controller and not with the people who booked or chased the
        # sale. An AR Manager who can both invoice and write off can make
        # any receivable disappear.
        *crud("sales", "invoicewriteoff"),
        *crud("sales", "depositapplication", actions=("view",)),
        "sales.write_off_invoice",
    ],
    "Sales Rep": [
        *REFERENCE,
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
        *crud("accounting", "chargetype", actions=("view",)),
        *crud("sales", "approvalpolicy", actions=("view",)),
        # deliberately NOT sales.approve_order: a rep cannot sign off
        # their own discount.
        *crud("sales", "customerprofile", actions=("view",)),
        *crud("sales", "quotation"),
        *crud("sales", "quotationline"),
        # deliberately NOT sales.post_invoice
    ],
    "AR Manager": [
        *REFERENCE,
        *crud("sales", "salesorder"),
        *crud("sales", "salesorderline"),
        *crud("sales", "invoice"),
        *crud("sales", "invoiceline"),
        *crud("sales", "invoicepayment"),
        *crud("sales", "pricelist"),
        *crud("sales", "pricelistitem"),
        *crud("accounting", "chargetype"),
        *crud("sales", "approvalpolicy"),
        *crud("sales", "customerprofile"),
        *crud("sales", "quotation"),
        *crud("sales", "quotationline"),
        *crud("sales", "dunninglevel"),
        *crud("sales", "commissionplan"),
        *crud("sales", "salesrep"),
        *crud("sales", "recurringinvoice"),
        *crud("sales", "recurringinvoiceline"),
        *crud("sales", "dunningnotice", actions=("view",)),
        *crud("sales", "invoicewriteoff", actions=("view",)),
        *crud("sales", "depositapplication", actions=("view",)),
        *crud("accounting", "payment"),
        "sales.post_invoice",
        "sales.approve_order",
        "accounting.post_payment",
    ],
    "Purchasing Clerk": [
        *REFERENCE,
        *crud("purchasing", "purchaseorder"),
        *crud("purchasing", "purchaseorderline"),
        *crud("purchasing", "bill"),
        *crud("purchasing", "billline"),
        *crud("core", "party", actions=("add", "change", "view")),
        *crud("core", "address", actions=("add", "change", "view")),
        *crud("core", "contact", actions=("add", "change", "view")),
        *crud("core", "partybankaccount", actions=("add", "change", "view")),
        *crud("core", "paymentterms", actions=("view",)),
        *crud("purchasing", "requestforquotation"),
        *crud("purchasing", "rfqline"),
        *crud("purchasing", "rfqinvitation"),
        *crud("purchasing", "rfqquote"),
        *crud("purchasing", "reorderrule"),
        *crud("purchasing", "budget", actions=("view",)),
        *crud("purchasing", "vendorprice", actions=("view",)),
        *crud("purchasing", "purchaserequisition", actions=("change", "view")),
        *crud("purchasing", "purchaserequisitionline", actions=("view",)),
        *crud("purchasing", "blanketorder"),
        *crud("purchasing", "blanketorderline"),
        *crud("purchasing", "purchaseapprovalpolicy", actions=("view",)),
        *crud("purchasing", "billpayment", actions=("view",)),
        # deliberately NOT purchasing.approve_purchaseorder: a buyer
        # cannot sign off their own spend.
        *crud("purchasing", "prepaymentapplication", actions=("view",)),
        # deliberately NOT purchasing.post_bill, and no payment allocation:
        # whoever raises the bill must not also be able to pay it.
    ],
    "AP Manager": [
        *REFERENCE,
        *crud("purchasing", "purchaseorder"),
        *crud("purchasing", "purchaseorderline"),
        *crud("purchasing", "bill"),
        *crud("purchasing", "billline"),
        *crud("purchasing", "requestforquotation"),
        *crud("purchasing", "rfqline"),
        *crud("purchasing", "rfqinvitation"),
        *crud("purchasing", "rfqquote"),
        *crud("purchasing", "landedcostapplication", actions=("view",)),
        *crud("purchasing", "reorderrule"),
        *crud("purchasing", "budget"),
        *crud("purchasing", "vendorprice"),
        *crud("purchasing", "purchaserequisition"),
        *crud("purchasing", "purchaserequisitionline"),
        "purchasing.decide_purchaserequisition",
        *crud("purchasing", "blanketorder"),
        *crud("purchasing", "blanketorderline"),
        *crud("purchasing", "purchaseapprovalpolicy"),
        *crud("purchasing", "approvaltier"),
        *crud("purchasing", "billpayment"),
        "purchasing.approve_purchaseorder",
        *crud("purchasing", "prepaymentapplication", actions=("view",)),
        *crud("accounting", "payment"),
        "purchasing.post_bill",
        "accounting.post_payment",
    ],
    "Warehouse Staff": [
        *REFERENCE,
        *crud("inventory", "warehouse", actions=("view",)),
        *crud("inventory", "item", actions=("view",)),
        *view("inventory", "stockmovement"),
        *crud("purchasing", "goodsreceipt"),
        *crud("purchasing", "goodsreceiptline"),
        *crud("purchasing", "receiptinspection", actions=("view",)),
        "purchasing.post_goodsreceipt",
        *crud("sales", "delivery"),
        *crud("sales", "deliveryline"),
        "sales.post_delivery",
        # A new batch is named as it comes in.
        *crud("inventory", "lot", actions=("add", "change", "view")),
        # What to receive against and what to ship.
        *view("purchasing", "purchaseorder", "purchaseorderline"),
        *view("sales", "salesorder", "salesorderline"),
        *view("inventory", "stockmovement", "stockposition", "storagebin"),
    ],
    # -- the plant ------------------------------------------------------
    "Production Supervisor": [
        *REFERENCE,
        *HOW_IT_IS_MADE,
        *view("inventory", "stockmovement", "stockposition", "storagebin"),
        *view("hr", "employee"),
        *crud("manufacturing", "workorder", actions=("add", "change", "view")),
        *full("manufacturing", "workorderoperation", "workordercomponent",
              "workordersubstitute", "materialissue", "materialissueline",
              "productionentry", "productionscrap", "productionbyproduct", "timebooking",
              "downtime", "operationreport", "rebatch", "rebatchline", "bale", "baleline",
              "bagcount", "tapecount", "tapedoff", "tapeload", "fabricroll", "filmroll",
              "processroll", "rollmount", "loomwaste", "machineclock", "crewassignment",
              "meterreading", "spareissue", "toolusage"),
        *crud("manufacturing", "maintenancejob", actions=("add", "change", "view")),
        *crud("manufacturing", "scalereading", actions=("add", "view")),
        *view("manufacturing", "loomstation", "stationattempt", "maintenanceschedule",
              "energymeter", "coatingcheck", "testcertificate", "complaint"),
        "manufacturing.weigh_at_station",
        # deliberately NOT the specifications, bills, routings or rates: the
        # floor makes what was specified, and a supervisor who could change
        # the bill could make any variance disappear. Nor costing.
    ],
    # The shared login on a station tablet. Operators say who they are
    # with their PIN; the login itself can do nothing else.
    "Station": [
        "manufacturing.weigh_at_station",
        "manufacturing.view_loomstation",
        *crud("manufacturing", "scalereading", actions=("add", "view")),
    ],
    "Production Planner": [
        *REFERENCE,
        *HOW_IT_IS_MADE,
        *full("planning", "forecast", "masterscheduleentry", "planneddemand",
              "plannedorder", "planningaction", "planningrun", "transferroute"),
        *crud("planning", "planningsettings", actions=("change", "view")),
        *crud("manufacturing", "workorder", actions=("add", "change", "view")),
        *crud("manufacturing", "workorderoperation", actions=("change", "view")),
        *full("manufacturing", "changeoverrule", "setupfamily", "alternaterouting"),
        *crud("manufacturing", "tool", actions=("change", "view")),
        *view("manufacturing", "crewassignment", "maintenancejob", "maintenanceschedule",
              "productionentry", "downtime"),
        *view("inventory", "stockmovement", "stockposition", "stockreservation"),
        *view("sales", "salesorder", "salesorderline", "calloff", "quotation"),
        *view("purchasing", "purchaseorder", "purchaseorderline", "vendorprice"),
        *crud("purchasing", "purchaserequisition", actions=("add", "view")),
        *crud("purchasing", "purchaserequisitionline", actions=("add", "view")),
        # deliberately NOT production entries or material issues: the plan
        # says what should happen, the floor records what did.
    ],
    "Quality Inspector": [
        *REFERENCE,
        *HOW_IT_IS_MADE,
        *crud("quality", "inspection", actions=("add", "change", "view")),
        *crud("quality", "reading", actions=("add", "change", "view")),
        *crud("quality", "calibration", actions=("add", "view")),
        *view("quality", "instrument", "characteristic", "inspectionplan", "planline"),
        *crud("manufacturing", "coatingcheck", actions=("add", "view")),
        *crud("manufacturing", "testcertificate", actions=("add", "view")),
        *crud("purchasing", "receiptinspection", actions=("add", "change", "view")),
        *view("manufacturing", "workorder", "bagcount", "fabricroll", "processroll",
              "productionentry", "complaint", "correctiveaction"),
        *view("purchasing", "goodsreceipt", "goodsreceiptline"),
        *view("sales", "thirdpartyrelease", "delivery"),
        *view("inventory", "stockmovement"),
        # deliberately NOT the plans, the limits or the instruments: whoever
        # takes the reading does not also set the pass mark.
    ],
    "Quality Manager": [
        *REFERENCE,
        *HOW_IT_IS_MADE,
        *full("quality", "inspection", "reading", "calibration", "instrument",
              "characteristic", "inspectionplan", "planline"),
        *crud("quality", "qualitysettings", actions=("change", "view")),
        *full("manufacturing", "complaint", "complaintlot", "correctiveaction",
              "coatingcheck", "testcertificate"),
        *full("purchasing", "receiptinspection"),
        *full("sales", "thirdpartyrelease", "thirdpartyreleaseline"),
        *view("manufacturing", "workorder", "bagcount", "fabricroll", "processroll",
              "productionentry"),
        *view("purchasing", "goodsreceipt", "goodsreceiptline"),
        *view("sales", "delivery", "deliveryline", "salesorder"),
        *view("inventory", "stockmovement"),
    ],
    "GST Officer": [
        *REFERENCE,
        "gst.compile_returns",
        *crud("gst", "einvoice", actions=("add", "change", "view")),
        *crud("gst", "ewaybill", actions=("add", "change", "view")),
        *full("gst", "unitquantitycode"),
        *view("accounting", "gstsettings", "journalentry", "journalline"),
        *view("sales", "invoice", "invoiceline", "invoicelinetax", "delivery"),
        *view("purchasing", "bill", "billline", "billlinetax"),
        *view("manufacturing", "jobworkchallan", "jobworkline", "jobworkloss"),
        # deliberately NOT posting or editing an invoice or a bill: the
        # person who files the return reports what was booked, and does
        # not book it.
    ],
    "HR Admin": [
        *REFERENCE,
        *crud("hr", "department"),
        *crud("hr", "employee"),
        *crud("hr", "leaverequest"),
        *view("hr", "leavepolicy"),
        "hr.decide_leaverequest",
        "hr.view_every_leaverequest",
        # Anyone's leave, as HR, a manager's or not.
        "hr.decide_any_leaverequest",
    ],
    "Payroll Officer": [
        *REFERENCE,
        *view("hr", "department", "employee", "leaverequest", "leavepolicy", "payslip", "payslipline",
              "statutoryremittance"),
        # Unpaid leave is pay: payroll reads everyone's.
        "hr.view_every_leaverequest",
        *full("hr", "paycomponent", "paycomponentslab", "employeecompensation"),
        *crud("hr", "payrun", actions=("add", "change", "view")),
        # deliberately NOT hr.post_payrun: whoever works the payroll out
        # does not also put it in the ledger and pay it.
    ],
    # Decides their own team's leave: the model refuses anyone they do
    # not manage, and the list shows only their reports'.
    "Line Manager": [
        *view("hr", "department", "employee", "leaverequest", "leavepolicy"),
        "hr.decide_leaverequest",
    ],
    "Employee Self Service": [
        *crud("purchasing", "purchaserequisition", actions=("add", "change", "view")),
        *crud("purchasing", "purchaserequisitionline", actions=("add", "change", "view")),
        # deliberately NOT decide_purchaserequisition: nobody approves
        # their own request.
        *crud("hr", "leaverequest", actions=("add", "view")),
        *view("hr", "leavepolicy"),
        # deliberately NOT hr.decide_leaverequest
    ],
}


class Command(BaseCommand):
    help = "Create or update the default role groups and their permissions."

    @transaction.atomic
    def handle(self, *args, **options):
        verbosity = options["verbosity"]
        missing = []
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
                    missing.append(f"{role_name}: {name}")
            group.permissions.set(permissions)
            if verbosity:
                verb = "created" if created else "updated"
                self.stdout.write(f"{verb} {role_name} ({len(permissions)} permissions)")
        if missing:
            # A misspelt permission used to be printed and passed over, and
            # the role went out a right short. Nothing is saved instead.
            raise CommandError("No such permission, so no roles were changed:\n  "
                               + "\n  ".join(missing))
