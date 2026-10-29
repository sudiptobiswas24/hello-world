"""Group a Django test log's failures by test module and the last error line."""
import re
import sys
from collections import Counter, defaultdict

text = open(sys.argv[1]).read()
blocks = re.split(r"\n={70}\n", text)
by_module = defaultdict(Counter)
for block in blocks[1:]:
    head = re.match(r"(ERROR|FAIL): (\w+) \(([\w.]+)\)", block)
    if not head:
        continue
    module = head.group(3).rsplit(".", 1)[0]
    lines = [l for l in block.split("\n") if l.strip()]
    last = next((l for l in reversed(lines) if re.match(r"^\w*(Error|Exception)\b|^AssertionError|^django\.", l)), lines[-1])
    by_module[module][last.strip()[:200]] += 1
for module in sorted(by_module):
    for message, n in by_module[module].most_common():
        print(f"{n:3d}  {module}  {message}")
