# fix_sec report

Worktree: scratchpad/fix_sec, branched from ef7c0c3. Tip 3b76f30. 12 commits, none pushed.

## Commits and the ids each closes

Wave 1:
- e60d37b O86: nobody gives a login more than they hold. O142 later amended this.
- bed3067 O83: every action that writes names its right. Nothing falls back to add_.
- fedef7f O85: nobody approves what they raised. The owner confirmed this as built.
- 0d924c0 O84, O119, O120: a rep reads and writes only about their own customers.
- 900d60e O88: a person's own records are read and written as theirs.
- 404753a O87: the admin's "delete selected" asks each row's own delete().

Wave 2 (the review):
- 0bd52ab O142: two people give a role; taking access away needs one.
- e73b502 O143: a quote's revision counts its writer as the order's raiser.
- 10d9ff6 O144: requisition cancel, converted lead owner, and login linking each go by the right person.
- 689bac7 O128: TDS reverse and challan void take purchasing.post_bill; goods-receipt accept and reject take change_receiptinspection.
- 3b76f30: tests name the five places the fixes differ from the probes on purpose. Closes no id.

## Test runs at the tip

- Touched modules: 2,547 tests OK (core, hr, imports, purchasing, quality, sales, planning firming, web, e2e lifecycle).
- New tests on PostgreSQL (erp_fixsec): 79 OK.
- Browser tests for logins, people, purchasing and CRM: 14 OK.
- The reviewer's probe file tests_probe_review.py: 8 OK.
- audit_invariants: no findings. makemigrations --check: no changes.

## Wave 2: tests that failed without the fix

- O142, TakingAccessAwayIsTheKeepersTests: "(403, True) != (200, False)" when deactivating a leaving rep, and "(403, [<Group: Controller>]) != (200, [])" when taking a role away.
- O142, TwoPeopleGiveARoleTests: "400 != 200 : ... Bookkeeper is given by someone who holds it, and you do not."
- O143, SelfApprovalThroughARevisionTests: "200 != 400" accepting the revision with approval, for both the quote's writer and the manager who revised it.
- O144, WhoCallsARequestOffTests: "(200, 'cancelled') != (403, APPROVED)".
- O144, ConvertedLeadTests: "['Gamma Cement'] != []".
- O144, ALoginIsLinkedToAPersonAsTheImportLinksOneTests: "(200, True) != (400, False)".
- O128, UndoingTakesTheDecisionRightTests: all four routes let a login holding only add_ and view_ past the gate ("-> 404").

Wave 1 failure lines are in each commit message.

## O142 as built (the owner's rule)

- `core.RoleProposal` (migration core 0031) and `apps/core/roles.py`. `may_give` is the one function that says who gives a role alone: today, a holder of the role or a superuser.
- `give_or_propose` gives a role the keeper holds at once. A role they do not hold is only proposed.
- A proposal is confirmed by someone who holds the role, never by its proposer, and never on one's own login. Decline and withdraw are the reverse path.
- API: /api/core/role-proposals/, with `?waiting=true` for one's own queue. Confirm and decline need core.confirm_roleproposal, which setup_roles gives every role; the record decides who may act.
- Screens: "Roles to confirm" for the holders, a "Roles proposed" panel on the login page, and an inbox count.
- Deactivate, revoke and grant skip `check_may_administer`. Password, email and reactivate still ask it.

## Judgement calls

- O128 goods-receipt reject: I mapped it to change_receiptinspection, not post_goodsreceipt. post_goodsreceipt would have handed the decision to the stores and taken it from quality, against the module's own rule ("the stores clerk does not decide"). Accept is mapped the same way, as its mirror. Today the add_ and change_ rights are held by the same people, so nobody gains or loses access.
- O144 requisition cancel: someone without the right is refused 403, which matches the probe. Leave cancel still refuses with 400.
- O144 linking a login to a person: "no rights beyond the employee's own" is read as the Employee Self Service role only (`users_api.EMPLOYEES_OWN`).

## What I could not do

- docs/IMPORT.md still lists the employees import's roles column. docs/ is not mine to edit.

## Seen, not fixed

- core/users_api.py, UserSerializer.create (security): the keeper sets a new login's first password. Once a holder confirms a proposed role, the keeper can still sign in as that login until the person changes the password. Example: HR makes asha with Bookkeeper proposed, a bookkeeper confirms, and HR still knows asha's first password.
- core/history_api.py:34 (security, minor): history of a deleted record is scoped only by the party scope. A colleague holding view_leaverequest but no limit from that scope (that is, a holder of sales.view_every_customer) reads the history of someone's deleted sick leave. Self Service is limited by the sales scope, so the probe passes only by accident.
- core/scoping.py:79 (minor): `scoped()` with a path filters on `path__in`, which drops rows whose path is NULL.
- inventory WarehouseViewSet (security, minor): `held_for` names a customer to every rep. The audit check lists it as reported.
- Leave screen (minor): "Cancel it" is not offered to a manager whom the server allows to cancel.
