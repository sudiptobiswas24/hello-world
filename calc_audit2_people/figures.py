"""Plain-Python expected figures for the audit2_people probes (no Django)."""
from decimal import Decimal as D, ROUND_HALF_UP

def rupee(x): return x.quantize(D(1), rounding=ROUND_HALF_UP)

# T1: fortnightly runs, 8,000 basic each; EPF wage ceiling is a month's wages: 15,000
month_wages = D(8000) * 2
pf_month = rupee(min(month_wages, D(15000)) * D(12) / 100)
print("T1 PF for the month", pf_month)               # 1800
print("T1 PF if ceiling per run", 2 * rupee(min(D(8000), D(15000)) * D(12) / 100))  # 1920

# T3: 20 days a year, carried over 10 with a policy limit of 5 -> 25
print("T3", D(20) + min(D(10), D(5)))


# ---- plant month, June 2026: default pattern 12345 -> 22 working days; basic and HRA both reduce
from decimal import ROUND_CEILING
def up(x): return x.quantize(D(1), rounding=ROUND_CEILING)
def pt(base):
    if base > 10000: return D(200)
    if base > 7500: return D(175)
    return D(0)
def paisa(x): return x.quantize(D("0.01"), rounding=ROUND_HALF_UP)
def slip(code, basic, hra_full, days, full=22):
    prop = D(days) / D(full)
    b_r, h = paisa(D(basic) * prop), paisa(D(hra_full) * prop)
    pf = rupee(min(b_r, D(15000)) * D(12) / 100)
    wages = b_r + h
    covered = wages <= D(21000)
    esi = up(wages * D("0.0075")) if covered else D(0)
    esi_er = up(wages * D("0.0325")) if covered else D(0)
    return dict(code=code, basic=b_r, hra=h, gross=wages, pf=pf, pf_er=pf, esi=esi, esi_er=esi_er, pt=pt(wages),
                eps=rupee(min(b_r, D(15000)) * D("8.33") / 100))
rows = []
for i in range(24):
    basic = 9000 + i * 750
    rows.append(slip(f"P{i:02d}", basic, int(basic * 0.4), 22))
rows.append(slip("J", 20000, 8000, 11))
rows.append(slip("L", 18000, 7200, 11))
rows.append(slip("U", 16000, 6400, 19))
rows.append(slip("ABS", 12000, 4800, 20))
tot = lambda k: sum(r[k] for r in rows)
net = sum(r["gross"] - r["pf"] - r["esi"] - r["pt"] for r in rows)
print({k: str(tot(k)) for k in ("gross", "pf", "pf_er", "esi", "esi_er", "pt", "eps")}, "net", net)
import json
json.dump({r["code"]: {k: str(v) for k, v in r.items()} for r in rows}, open("calc_audit2_people/june.json", "w"))
