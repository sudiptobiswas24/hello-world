"""
A claim a test prints is a claim it checks.

The fact tests printed each claim with PASS or FAIL through a say() of their own that printed and returned, in
four modules. A false claim printed FAIL and the run stayed green: from g40 to g47 every lane printed
"FAIL O109 sales: 490 + 490 within the days ..." (O250), and nothing failed. One say(), here, so no copy of it
can go back to printing only.
"""


def say(ok, claim, detail=""):
    """Print the claim, PASS or FAIL, and fail the test on a false one."""
    said = claim + (f"  [{detail}]" if detail else "")
    print(("PASS " if ok else "FAIL ") + said)
    if not ok:
        raise AssertionError(said)
