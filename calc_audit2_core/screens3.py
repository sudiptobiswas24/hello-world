import os, re, pathlib
os.environ.setdefault("DJANGO_SETTINGS_MODULE","config.settings")
import django; django.setup()
from django.urls import resolve, Resolver404
from apps.core.management.commands.setup_roles import ROLES
src = pathlib.Path("frontend/src")
reg = (src/"app/registry.ts").read_text()
def need_of(v):
    rp=getattr(v,"required_permission",None)
    if rp: return rp
    qs=getattr(v,"queryset",None)
    if qs is not None:
        mt=qs.model._meta; return f"{mt.app_label}.view_{mt.model_name}"
    return None
out=[]
for m in re.finditer(r'path: "([^"]+)", label: "([^"]+)", permission: "([^"]+)"(.*?)load: \(\) => import\("\.\./([^"]+)"\)', reg, re.S):
    path,label,perm,mid,mod = m.groups()
    f = src/(mod+".tsx")
    text = f.read_text() if f.exists() else ""
    also = re.findall(r'also: \[([^\]]*)\]', mid)
    alsop = re.findall(r'"([^"]+)"', also[0]) if also else []
    keep = re.findall(r'keep: "([^"]+)"', mid)
    eps=set()
    for u in re.findall(r'["`](/api/[a-z0-9_/-]+?/)(?:\$\{|["`])', text):
        try:
            r=resolve(u); v=getattr(r.func,"cls",None)
            n=need_of(v) if v else None
            if n: eps.add((u,n))
        except Resolver404: pass
    for role,perms in ROLES.items():
        ps=set(perms)
        if perm in ps and all(a in ps for a in alsop) and (not keep or keep[0] in ps):
            lacking=sorted(u+" needs "+n for u,n in eps if n not in ps)
            if lacking: out.append((role,path,lacking))
import collections
for role,path,l in out: print(role,"|",path,"|",l)
