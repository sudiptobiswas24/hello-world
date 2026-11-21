"""O613: the demo's draft bill of entry, worked by hand. Duty is to the rupee, line by line."""
from decimal import Decimal as D, ROUND_HALF_UP
rupee = lambda x: x.quantize(D("1"), ROUND_HALF_UP)
paisa = lambda x: x.quantize(D("0.01"), ROUND_HALF_UP)
fob = [D("25000") * D("1.10"), D("18000") * D("1.25")]           # 27,500 and 22,500 USD
cif = (sum(fob) + D("1150") + D("52")) * D("84.10")               # freight and insurance, at the customs rate
values = [paisa(cif * f / sum(fob)) for f in fob]
bcd = [rupee(v * D("0.075")) for v in values]
sws = [rupee(b * D("0.10")) for b in bcd]
igst = [rupee((v + b + s) * D("0.18")) for v, b, s in zip(values, bcd, sws)]
print("assessable", paisa(cif), values)
print("bcd", bcd, sum(bcd), "sws", sws, sum(sws), "duty", sum(bcd) + sum(sws), "igst", igst, sum(igst))
