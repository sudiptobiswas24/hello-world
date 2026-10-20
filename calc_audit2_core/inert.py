import re, pathlib, collections
root=pathlib.Path("apps")
files=[p for p in root.rglob("*.py") if "migrations" not in p.parts and "/tests" not in str(p) and not p.name.startswith("tests")]
text={p:p.read_text() for p in files}
allsrc="\n".join(text.values())+"\n".join(p.read_text() for p in pathlib.Path("frontend/src").rglob("*.ts*"))+"\n".join(p.read_text() for p in pathlib.Path("deploy").rglob("*") if p.is_file() and p.suffix in ('.py','.sh'))+pathlib.Path("RUNBOOK.md").read_text()
templates="\n".join(p.read_text() for p in pathlib.Path("apps").rglob("*.html"))
allsrc+=templates
WORDS=collections.Counter(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", allsrc))
scope=[p for p in files if p.parts[1] in ("core","assets","planning","imports")]
out=[]
for p in scope:
    for m in re.finditer(r'^\s*def ([a-z][a-zA-Z0-9_]+)\(', text[p], re.M):
        name=m.group(1)
        if name.startswith("__") or name in ("get_queryset","get_serializer_class","perform_create","perform_update","perform_destroy","save","delete","clean","handle","add_arguments","ready","has_permission","list","create","retrieve","update","destroy","partial_update","validate","to_representation","authenticate","get_object","filter_queryset","get_permissions","has_object_permission"): continue
        n=WORDS[name]
        if n<=1: out.append((str(p),name))
for o in out: print(*o)
