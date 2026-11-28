"""Figures the floor probes assert, worked in plain Python before the probes were run."""
from decimal import Decimal

# F1: 20 rounds of 4 wrong PINs and one right one. A lock after 5 wrong in 10 minutes allows 4 wrong
# guesses at most before the 5th is refused; with the reset-on-success rule 80 are tried, none refused as locked.
rounds, wrong_per_round = 20, 4
print("wrong guesses a lockout should have stopped at 5:", rounds * wrong_per_round)

# F4: 104.4 kg net on a 87 gsm, 60 cm tubular roll supports 1000 m (104.4 g a metre); declared 1006 is +0.60 %.
net = Decimal("106.8") - Decimal("2.4")
per_metre = Decimal("87") * Decimal("0.6") * 2 / 1000
print("net kg", net, "metres", net / per_metre * 1000 / 1000 * Decimal(1000) / Decimal(1000) if False else net / per_metre)
print("variance %", ((Decimal("1006") - net / per_metre) / (net / per_metre) * 100).quantize(Decimal("0.01")))
# After a patch to 2000 m the stored variance stays +0.60 % while the roll says 2000 m: 100 % off.
print("true variance after edit %", ((Decimal("2000") - net / per_metre) / (net / per_metre) * 100).quantize(Decimal("0.01")))
