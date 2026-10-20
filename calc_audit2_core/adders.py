import os, re, pathlib
os.environ.setdefault("DJANGO_SETTINGS_MODULE","config.settings")
import django; django.setup()
from django.urls import resolve
src = pathlib.Path("frontend/src/modules")
pat = re.compile(r'url: \(\) => "(/api/[a-z-]+/[a-z-]+/)",\s*fields: (?:\([^)]*\) => )?\[(.*?)\],\s*body: \([^)]*\) => (\(\{.*?\}\)|values)', re.S)
tot=hit=0
for p in sorted(src.rglob("*.tsx")):
    t=p.read_text()
    n=len(re.findall(r'adder:\s*\{|adders:|label: "Add ', t))
    h=len(pat.findall(t))
    if n: print(f"{p.name:34} adders~{n} matched {h}")
