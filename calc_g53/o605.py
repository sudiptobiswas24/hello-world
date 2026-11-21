"""O605: the demo floor's figures, worked by hand from the loader's inputs."""
from decimal import Decimal as D, ROUND_HALF_UP

q2 = lambda x: x.quantize(D("0.01"), ROUND_HALF_UP)
q4 = lambda x: x.quantize(D("0.0001"), ROUND_HALF_UP)

# Rolls: F-50 is 50 cm lay-flat, tubular (2 layers), 87.5 GSM; the core weighs 2.40 kg.
per_metre_g = D("87.5") * 2 * D("0.50")              # 87.5 g a metre
rolls = [("90.00", "1000"), ("89.10", "995"), ("91.30", "1010"), ("88.40", "1060"), ("90.50", "1005")]
nets = [D(g) - D("2.40") for g, _ in rolls]
derived = [q2(n * 1000 / per_metre_g) for n in nets]
declared = [D(m) for _, m in rolls]
print("nets", nets, "total", sum(nets))
print("metres from weight", derived, "total", sum(derived), "declared", sum(declared))
print("loom variance %", q2((sum(declared) - sum(derived)) / sum(derived) * 100), "tolerance 2.00")
for d, m in zip(derived, declared):
    print("  roll", m, "variance %", q2((m - d) / d * 100), "over" if abs((m - d) / d * 100) > 2 else "")

# Operator yield: ideal = quantity / the work centre's rate an hour * 60, kept at 4 places; performance ideal/minutes.
for who, centre, qty, rate, minutes in (("302", "EXT", "1940", "450", "300"), ("303", "LOOM", sum(nets), "360", "86"),
                                        ("302", "PRINT", "1050", "6000", "13"), ("304", "CONV", "1000", "5000", "14")):
    ideal = q4(D(qty) / D(rate) * 60)
    print(who, centre, "minutes", minutes, "ideal", ideal, "performance", q4(ideal / D(minutes)))

# Steps: the press counted 1,050 good; the stitching line 1,000 good and 18 + 12 scrap: 1,030 of 1,050, 20 waiting.
print("stitching takes", 1000 + 18 + 12, "of", 1050)
